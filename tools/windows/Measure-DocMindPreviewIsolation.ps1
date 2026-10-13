[CmdletBinding()]
param(
    [ValidateRange(1, 86400)][int]$DurationSeconds = 900,
    [ValidateRange(1, 60)][int]$IntervalSeconds = 1,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$allowed = Join-Path $repo '.local'
$destination = [IO.Path]::GetFullPath($OutputDirectory)
if (-not $destination.StartsWith($allowed + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'MEASUREMENTS_REQUIRE_IGNORED_LOCAL_PATH' }
if (Test-Path -LiteralPath $destination) { throw 'MEASUREMENT_DIRECTORY_ALREADY_EXISTS' }
[IO.Directory]::CreateDirectory($destination) | Out-Null
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
Protect-DocMindJobDirectory -LiteralPath $destination
function ConvertTo-WslPath([string]$Path) { return '/mnt/' + $Path.Substring(0, 1).ToLowerInvariant() + $Path.Substring(2).Replace('\', '/') }
$probe = ConvertTo-WslPath (Join-Path $repo 'app/scripts/preview_worker_isolation_probe.py')
$linuxOutput = ConvertTo-WslPath (Join-Path $destination 'wsl.jsonl')
$arguments = '-d Ubuntu -- python3 "{0}" sample-wsl --duration {1} --interval {2} --output "{3}"' -f $probe, $DurationSeconds, $IntervalSeconds, $linuxOutput
$linux = Start-Process wsl.exe -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $destination 'collector-error.log') -RedirectStandardOutput (Join-Path $destination 'collector.log')
$null = $linux.Handle
$clock = [Diagnostics.Stopwatch]::StartNew()
$windowsFile = Join-Path $destination 'windows.jsonl'
try {
    do {
        $sampleClock = [Diagnostics.Stopwatch]::StartNew()
        $row = [ordered]@{ utc = [DateTimeOffset]::UtcNow.ToString('o'); elapsed_seconds = $clock.Elapsed.TotalSeconds; interval_seconds = $IntervalSeconds; windows_available_bytes = $null; committed_bytes = $null; commit_limit_bytes = $null; workers = @() }
        try {
            $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
            $row.windows_available_bytes = [Int64]$memory.AvailableBytes
            $row.committed_bytes = [Int64]$memory.CommittedBytes
            $row.commit_limit_bytes = [Int64]$memory.CommitLimit
            $workerIds = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -match 'Start-DocMindUEncryptorHostWorker|run-.*worker\.ps1' } | ForEach-Object ProcessId)
            foreach ($workerId in $workerIds) {
                $worker = Get-Process -Id $workerId -ErrorAction SilentlyContinue
                if ($worker) { $row.workers += @{ pid = $workerId; private_bytes = $worker.PrivateMemorySize64; working_set_bytes = $worker.WorkingSet64 } }
            }
        } catch { $row.collection_error = 'WINDOWS_SAMPLE_FAILED' }
        [IO.File]::AppendAllText($windowsFile, (($row | ConvertTo-Json -Compress -Depth 6) + "`n"), [Text.UTF8Encoding]::new($false))
        if ($clock.Elapsed.TotalSeconds -ge $DurationSeconds) { break }
        Start-Sleep -Milliseconds ([int][Math]::Max(1, $IntervalSeconds * 1000 - $sampleClock.ElapsedMilliseconds))
    } while ($true)
    if (-not $linux.WaitForExit(30000) -or $linux.ExitCode -ne 0) { throw 'WSL_SAMPLER_FAILED' }
    Write-Output 'Memory samples saved. Evaluate both streams; missing diagnostics fail the deployment gate.'
} finally {
    if (-not $linux.HasExited) { Stop-Process -Id $linux.Id -Force }
    $linux.Dispose()
}
