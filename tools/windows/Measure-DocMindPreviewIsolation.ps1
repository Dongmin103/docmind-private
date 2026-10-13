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
function Get-DocMindMeasuredWorkerRole {
    param([string]$CommandLine)
    if ($CommandLine -match 'run-ingest\.ps1(?:["\s]|$)' -or $CommandLine -match '-Purpose\s+ingest\b') { return 'ingest' }
    if ($CommandLine -match 'run-preview\.ps1(?:["\s]|$)' -or $CommandLine -match '-Purpose\s+preview\b') { return 'preview' }
    if ($CommandLine -match 'Start-DocMindUEncryptorHostWorker|run-.*worker\.ps1') { return 'mixed' }
    return ''
}
$probe = ConvertTo-WslPath (Join-Path $repo 'app/scripts/preview_worker_isolation_probe.py')
$linuxOutput = ConvertTo-WslPath (Join-Path $destination 'wsl.jsonl')
$arguments = '-d Ubuntu -- python3 "{0}" sample-wsl --duration {1} --interval {2} --output "{3}"' -f $probe, $DurationSeconds, $IntervalSeconds, $linuxOutput
$linux = Start-Process wsl.exe -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $destination 'collector-error.log') -RedirectStandardOutput (Join-Path $destination 'collector.log')
$null = $linux.Handle
$clock = [Diagnostics.Stopwatch]::StartNew()
$windowsFile = Join-Path $destination 'windows.jsonl'
$expectedWorkerRoles = @()
foreach ($task in @(Get-ScheduledTask | Where-Object { $_.TaskName -match '^DocMind Host Worker' -and $_.Settings.Enabled })) {
    if ($task.TaskName -match '\((ingest|preview)\)$') { $expectedWorkerRoles += $Matches[1] } else { $expectedWorkerRoles += 'mixed' }
}
try {
    do {
        $sampleClock = [Diagnostics.Stopwatch]::StartNew()
        $row = [ordered]@{ utc = [DateTimeOffset]::UtcNow.ToString('o'); elapsed_seconds = $clock.Elapsed.TotalSeconds; interval_seconds = $IntervalSeconds; expected_worker_roles = $expectedWorkerRoles; windows_available_bytes = $null; committed_bytes = $null; commit_limit_bytes = $null; workers = @() }
        try {
            $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
            $row.windows_available_bytes = [Int64]$memory.AvailableBytes
            $row.committed_bytes = [Int64]$memory.CommittedBytes
            $row.commit_limit_bytes = [Int64]$memory.CommitLimit
            foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'")) {
                $role = Get-DocMindMeasuredWorkerRole -CommandLine $process.CommandLine
                if (-not $role) { continue }
                $worker = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
                if ($worker) { $row.workers += @{ role = $role; pid = $process.ProcessId; private_bytes = $worker.PrivateMemorySize64; working_set_bytes = $worker.WorkingSet64 } }
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
