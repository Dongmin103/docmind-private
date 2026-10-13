[CmdletBinding()]
param(
    [ValidateRange(1, 3600)][int]$DeliveryDelaySeconds = 540,
    [ValidateRange(1, 100)][int]$Trials = 20,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (-not $output.StartsWith((Join-Path $repo '.local') + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $output)) { throw 'FRESH_IGNORED_PROBE_DIRECTORY_REQUIRED' }
[IO.Directory]::CreateDirectory($output) | Out-Null
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
Protect-DocMindJobDirectory -LiteralPath $output
$server = $null; $parents = @()
function Wait-ProbeFile([string]$Path, [int]$TimeoutSeconds = 15) {
    $until = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not (Test-Path -LiteralPath $Path)) {
        if ([DateTimeOffset]::UtcNow -ge $until) { throw 'SYNTHETIC_PROBE_TIMEOUT' }
        Start-Sleep -Milliseconds 20
    }
}
try {
    $readyFile = Join-Path $output 'server.json'
    $probe = Join-Path $repo 'app/scripts/preview_worker_isolation_probe.py'
    $server = Start-Process python.exe -ArgumentList ('"{0}" serve-synthetic --delivery-delay {1} --output "{2}"' -f $probe, $DeliveryDelaySeconds, $readyFile) -WindowStyle Hidden -PassThru
    $null = $server.Handle
    Wait-ProbeFile $readyFile
    $port = (Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json).port
    $exe = Join-Path $output 'synthetic.exe'
    Add-Type -TypeDefinition 'using System.IO; using System.Threading; public class IsolationSynthetic { public static int Main(string[] args) { Thread.Sleep(30); File.Copy(args[0],args[1]); return 0; } }' -OutputAssembly $exe -OutputType ConsoleApplication
    $source = Join-Path $output 'synthetic.pdf'
    # A one-page, sub-1MiB fixture; no user document or encryption executable.
    $pdf = "%PDF-1.4`n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj`n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj`n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj`ntrailer<</Root 1 0 R>>`n%%EOF"
    [IO.File]::WriteAllText($source, $pdf, [Text.Encoding]::ASCII)
    $common = Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1'
    foreach ($mode in @('mixed','isolated')) {
        $helper = Join-Path $output ($mode + '-gate.ps1')
        [IO.File]::WriteAllText($helper, ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')).Replace('Global\DocMind-uEncryptor2-SingleRun', ('Local\DocMind-Probe-' + $mode + '-' + [Guid]::NewGuid().ToString('n')))))
    }
    $worker = Join-Path $output 'run-probe-worker.ps1'
    $workerText = @'
param($Common,$Helper,$Control,$Exe,$Source,$Output,$Mode,$Port,$Delay)
$ErrorActionPreference='Stop'
. $Common; . $Helper
Add-Type -AssemblyName System.Net.Http
$gate=Enter-DocMindDecryptGate -ControlRoot $Control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(10)) -ValidateLease { }
try {
    Invoke-DocMindDecryptProcess -Gate $gate -Executable $Exe -SourceFile $Source -OutputFile $Output -TimeoutSeconds 10
    if ($Mode -eq 'isolated') { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $true }
    [IO.File]::WriteAllText($Output+'.delivery','waiting')
    $client=[Net.Http.HttpClient]::new();$client.Timeout=[TimeSpan]::FromSeconds([int]$Delay+30)
    try {
        $response=$client.GetAsync(('http://127.0.0.1:{0}/synthetic-ingest' -f $Port)).GetAwaiter().GetResult()
        try { if (-not $response.IsSuccessStatusCode) { throw 'SYNTHETIC_DELIVERY_FAILED' } } finally { $response.Dispose() }
    } finally { $client.Dispose() }
    [IO.File]::WriteAllText($Output+'.ack','accepted')
} finally { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $gate.ProcessExitConfirmed }
'@
    [IO.File]::WriteAllText($worker, $workerText)
    foreach ($mode in @('mixed','isolated')) {
        $args = '-NoProfile -ExecutionPolicy Bypass -File "{0}" "{1}" "{2}" "{3}" "{4}" "{5}" "{6}" "{7}" {8} {9}' -f $worker,$common,(Join-Path $output ($mode+'-gate.ps1')),(Join-Path $output ($mode+'-control')),$exe,$source,(Join-Path $output ($mode+'-ingest.pdf')),$mode,$port,$DeliveryDelaySeconds
        $parent = Start-Process powershell.exe -ArgumentList $args -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $output ($mode+'-error.log'))
        $null = $parent.Handle; $parents += $parent
    }
    Wait-ProbeFile (Join-Path $output 'mixed-ingest.pdf.delivery')
    Wait-ProbeFile (Join-Path $output 'isolated-ingest.pdf.delivery')
    $mixedStart = [Diagnostics.Stopwatch]::StartNew()
    . (Join-Path $output 'mixed-gate.ps1')
    $blocked = $false
    try { $g = Enter-DocMindDecryptGate -ControlRoot (Join-Path $output 'mixed-control') -DeadlineUtc ([DateTimeOffset]::UtcNow.AddMilliseconds(250)) -ValidateLease { }; Exit-DocMindDecryptGate -Gate $g -ProcessExitConfirmed $true }
    catch { $blocked = $_.Exception.Message -eq 'PREVIEW_DECRYPT_BUSY' }
    if (-not $blocked) { throw 'MIXED_BASELINE_DID_NOT_BLOCK' }
    . (Join-Path $output 'isolated-gate.ps1')
    $durations = @()
    for ($trial = 0; $trial -lt $Trials; $trial++) {
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $gate = Enter-DocMindDecryptGate -ControlRoot (Join-Path $output 'isolated-control') -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(30)) -ValidateLease { }
        try { Invoke-DocMindDecryptProcess -Gate $gate -Executable $exe -SourceFile $source -OutputFile (Join-Path $output ('preview-'+$trial+'.pdf')) -TimeoutSeconds 10 }
        finally { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $gate.ProcessExitConfirmed }
        $durations += $clock.Elapsed.TotalMilliseconds
        if (Test-Path (Join-Path $output 'isolated-ingest.pdf.ack')) { throw 'PREVIEW_DID_NOT_PRECEDE_INGEST_ACK' }
    }
    Wait-ProbeFile (Join-Path $output 'mixed-ingest.pdf.ack') ($DeliveryDelaySeconds+30)
    . (Join-Path $output 'mixed-gate.ps1')
    $gate = Enter-DocMindDecryptGate -ControlRoot (Join-Path $output 'mixed-control') -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { }
    try { Invoke-DocMindDecryptProcess -Gate $gate -Executable $exe -SourceFile $source -OutputFile (Join-Path $output 'mixed-preview.pdf') -TimeoutSeconds 10 }
    finally { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $gate.ProcessExitConfirmed }
    $baselineMs = $mixedStart.Elapsed.TotalMilliseconds
    Wait-ProbeFile (Join-Path $output 'isolated-ingest.pdf.ack') 30
    foreach ($parent in $parents) { if (-not $parent.WaitForExit(10000) -or $parent.ExitCode -ne 0) { throw 'SYNTHETIC_WORKER_FAILED' } }
    $sorted = @($durations | Sort-Object); $p95 = $sorted[[int][Math]::Ceiling(.95 * $Trials)-1]
    $result = @{ passed=$true; delivery_delay_seconds=$DeliveryDelaySeconds; trials=$Trials; mixed_decrypt_ready_ms=$baselineMs; isolated_decrypt_ready_ms=$durations; isolated_decrypt_ready_p95_ms=$p95; browser_first_page_measured=$false; functional_release_gate_passed=$false }
    [IO.File]::WriteAllText((Join-Path $output 'result.json'), ($result | ConvertTo-Json -Depth 5))
    Write-Output ('PASS: actual HTTP delay {0}s; mixed decrypt-ready {1:F0}ms; isolated decrypt-ready p95 {2:F0}ms. Browser render remains unmeasured.' -f $DeliveryDelaySeconds,$baselineMs,$p95)
} finally {
    foreach ($parent in $parents) { if (-not $parent.HasExited) { Stop-Process -Id $parent.Id -Force }; $parent.Dispose() }
    if ($server) { if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }; $server.Dispose() }
}
