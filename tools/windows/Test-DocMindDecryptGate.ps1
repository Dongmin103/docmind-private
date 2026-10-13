[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$helper = Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1'
if (-not (Test-Path -LiteralPath $helper)) { throw 'Decrypt gate helper is missing.' }
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function Assert-Code([scriptblock]$Action, [string]$Code) { $caught = ''; try { & $Action } catch { $caught = $_.Exception.Message }; Assert-True ($caught -eq $Code) ('Expected ' + $Code + ', got ' + $caught) }
$base = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/decrypt-gate-tests'))
$root = Join-Path $base ([Guid]::NewGuid().ToString('n'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$isolatedHelper = Join-Path $root 'gate.ps1'
[IO.File]::WriteAllText($isolatedHelper, ([IO.File]::ReadAllText($helper).Replace('Global\DocMind-uEncryptor2-SingleRun', ('Local\DocMind-GateTest-' + [Guid]::NewGuid().ToString('n')))))
$helper = $isolatedHelper
. $helper

try {
    $exe = Join-Path $root 'synthetic.exe'
    Add-Type -TypeDefinition 'using System; using System.IO; using System.Threading; public class GateSynthetic { public static int Main(string[] a) { File.WriteAllText(a[1]+".started", "started"); Thread.Sleep(int.Parse(File.ReadAllText(a[0]))); File.WriteAllText(a[1], "synthetic"); return 0; } }' -OutputAssembly $exe -OutputType ConsoleApplication
    $inputFile = Join-Path $root 'input'; [IO.File]::WriteAllText($inputFile, '50')
    $control = Join-Path $root 'control'
    $gate = Enter-DocMindDecryptGate -ControlRoot $control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { }
    Invoke-DocMindDecryptProcess -Gate $gate -Executable $exe -SourceFile $inputFile -OutputFile (Join-Path $root 'out') -TimeoutSeconds 5
    Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $true
    Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $true
    Assert-True $gate.Released 'Gate not released.'
    # A sender may still be waiting here, while another actual process decrypts.
    $gate2 = Enter-DocMindDecryptGate -ControlRoot $control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { }
    Invoke-DocMindDecryptProcess -Gate $gate2 -Executable $exe -SourceFile $inputFile -OutputFile (Join-Path $root 'out2') -TimeoutSeconds 5
    Exit-DocMindDecryptGate -Gate $gate2 -ProcessExitConfirmed $true
    Assert-True (Test-Path (Join-Path $root 'out2')) 'Second decrypt did not finish before sender ACK.'
    $gate = Enter-DocMindDecryptGate -ControlRoot $control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { }
    [IO.File]::WriteAllText($inputFile, '10000')
    Assert-Code { Invoke-DocMindDecryptProcess -Gate $gate -Executable $exe -SourceFile $inputFile -OutputFile (Join-Path $root 'timeout') -TimeoutSeconds 1 } 'TIMEOUT_OR_INTERACTIVE_PROMPT'
    Assert-True $gate.ProcessExitConfirmed 'Timeout did not confirm process tree exit.'
    Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $gate.ProcessExitConfirmed
    # Two Windows worker processes exercise contention and an abandoned parent.
    $worker = Join-Path $root 'worker.ps1'
    $common = Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1'
    $scriptText = @'
param($Common, $Helper, $Control, $Exe, $InputFile, $OutputFile, $Mode)
$ErrorActionPreference = 'Stop'
. $Common; . $Helper
$gate = Enter-DocMindDecryptGate -ControlRoot $Control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(10)) -ValidateLease { }
try {
    Invoke-DocMindDecryptProcess -Gate $gate -Executable $Exe -SourceFile $InputFile -OutputFile $OutputFile -TimeoutSeconds 60
} finally { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $gate.ProcessExitConfirmed }
if ($Mode -eq 'delivery') { [IO.File]::WriteAllText($OutputFile + '.delivery', 'waiting'); Start-Sleep -Seconds 30 }
'@
    [IO.File]::WriteAllText($worker, $scriptText)
    function Start-SyntheticWorker([string]$ChildControl, [string]$Output, [string]$Mode) {
        $args = '-NoProfile -ExecutionPolicy Bypass -File "{0}" "{1}" "{2}" "{3}" "{4}" "{5}" "{6}" "{7}"' -f $worker, $common, $helper, $ChildControl, $exe, $inputFile, $Output, $Mode
        return Start-Process powershell.exe -ArgumentList $args -WindowStyle Hidden -PassThru
    }
    function Wait-File([string]$Path) {
        $until = [DateTimeOffset]::UtcNow.AddSeconds(10)
        while (-not (Test-Path -LiteralPath $Path)) { if ([DateTimeOffset]::UtcNow -gt $until) { throw 'Synthetic child did not start.' }; Start-Sleep -Milliseconds 50 }
    }
    [IO.File]::WriteAllText($inputFile, '50')
    $sender = Start-SyntheticWorker $control (Join-Path $root 'sender') 'delivery'
    try {
        Wait-File (Join-Path $root 'sender.delivery')
        $second = Start-SyntheticWorker $control (Join-Path $root 'second') 'exit'
        try { Assert-True ($second.WaitForExit(10000) -and $second.ExitCode -eq 0) 'Second process blocked behind delivery.' }
        finally { if (-not $second.HasExited) { Stop-Process -Id $second.Id -Force } }
        Assert-True (-not $sender.HasExited) 'Delivery was not overlapping second decrypt.'
    } finally { if (-not $sender.HasExited) { Stop-Process -Id $sender.Id -Force } }
    [IO.File]::WriteAllText($inputFile, '30000')
    $crashControl = Join-Path $root 'crash-control'
    $parent = Start-SyntheticWorker $crashControl (Join-Path $root 'orphan') 'exit'
    try {
        Wait-File (Join-Path $root 'orphan.started')
        Assert-Code { Enter-DocMindDecryptGate -ControlRoot $crashControl -DeadlineUtc ([DateTimeOffset]::UtcNow.AddMilliseconds(300)) -ValidateLease { } } 'PREVIEW_DECRYPT_BUSY'
        Assert-Code { Enter-DocMindDecryptGate -ControlRoot $crashControl -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { throw 'PREVIEW_STALE_WORKER_RESULT' } } 'PREVIEW_STALE_WORKER_RESULT'
        Stop-Process -Id $parent.Id -Force
        [void]$parent.WaitForExit(5000)
        Start-Sleep -Milliseconds 200
        Assert-True (-not (Test-Path (Join-Path $root 'orphan'))) 'Orphan completed after parent death.'
        $owner = Get-Content (Join-Path $crashControl 'owner.json') -Raw | ConvertFrom-Json
        Assert-True ($null -eq (Get-Process -Id $owner.child_pid -ErrorAction SilentlyContinue)) 'Child survived parent termination.'
        # The kernel may destroy the named mutex when its last handle closes.
        # Persisted ownership is still checked, even without an abandoned event.
        $gate = Enter-DocMindDecryptGate -ControlRoot $crashControl -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { }
        Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $true
    } finally { if (-not $parent.HasExited) { Stop-Process -Id $parent.Id -Force } }
    $reusedControl = Join-Path $root 'reused-control'; [IO.Directory]::CreateDirectory($reusedControl) | Out-Null
    Write-DocMindGateRecord -Path (Join-Path $reusedControl 'owner.json') -Record @{ phase = 'running'; child_pid = $PID; child_started_utc = '2000-01-01T00:00:00Z'; executable_sha256 = ('a' * 64) }
    Assert-Code { Enter-DocMindDecryptGate -ControlRoot $reusedControl -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { } } 'DECRYPT_GATE_BLOCKED'
    # Deliberate failed termination leaves a durable block, never a clean state.
    $failedControl = Join-Path $root 'failed-control'
    $gate = Enter-DocMindDecryptGate -ControlRoot $failedControl -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { }
    function Stop-DocMindDecryptChild { param($Child) return $false }
    Assert-Code { Invoke-DocMindDecryptProcess -Gate $gate -Executable $exe -SourceFile $inputFile -OutputFile (Join-Path $root 'failed') -TimeoutSeconds 1 } 'PROCESS_TERMINATION_FAILED'
    Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $false
    Assert-Code { Enter-DocMindDecryptGate -ControlRoot $failedControl -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { } } 'DECRYPT_GATE_BLOCKED'
    Assert-Code { Enter-DocMindDecryptGate -ControlRoot $control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { throw 'PREVIEW_STALE_WORKER_RESULT' } } 'PREVIEW_STALE_WORKER_RESULT'
    # Crash before PID publication must fail closed.
    [IO.File]::WriteAllText((Join-Path $control 'owner.json'), '{"phase":"starting"}')
    Assert-Code { Enter-DocMindDecryptGate -ControlRoot $control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { } } 'DECRYPT_GATE_BLOCKED'
    Assert-True (Test-Path (Join-Path $control 'blocked.json')) 'Incomplete owner was not persistently blocked.'
    Remove-Item -LiteralPath (Join-Path $control 'owner.json') -Force
    Assert-Code { Enter-DocMindDecryptGate -ControlRoot $control -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(5)) -ValidateLease { } } 'DECRYPT_GATE_BLOCKED'
} finally {
    if ([IO.Path]::GetFullPath($root).StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $root -Recurse -Force }
}
'PASS: real decrypt processes, delivery-independent release, timeout, stale lease, incomplete owner and persistent block.'
