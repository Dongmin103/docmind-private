[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
$base = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/preview-isolation-tests'))
$config = [pscustomobject]@{ work_root = (Join-Path $base 'ingest'); cleanup_receipt_root = (Join-Path $base 'ingest-receipts'); decrypt_control_root = (Join-Path $base 'control') }
$ingest = Get-DocMindWorkerRolePaths -Config $config -Purpose ingest
$config.work_root = Join-Path $base 'preview'; $config.cleanup_receipt_root = Join-Path $base 'preview-receipts'
$preview = Get-DocMindWorkerRolePaths -Config $config -Purpose preview
Assert-True ($ingest.WorkRoot -ne $preview.WorkRoot -and $ingest.ReceiptRoot -ne $preview.ReceiptRoot -and $ingest.ControlRoot -eq $preview.ControlRoot) 'Role paths do not isolate plaintext and receipts.'
$handle = Enter-DocMindWorkerSingleton -Purpose preview
try {
    # A second OS process, not recursive acquisition on the same thread.
    $common = Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1'
    $command = ". '$common'; try { Enter-DocMindWorkerSingleton -Purpose preview; exit 2 } catch { if (`$_.Exception.Message -eq 'WORKER_ALREADY_RUNNING') { exit 0 }; exit 3 }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    & powershell.exe -NoProfile -EncodedCommand $encoded
    Assert-True ($LASTEXITCODE -eq 0) 'Duplicate role worker was allowed.'
    $other = Enter-DocMindWorkerSingleton -Purpose ingest
    $other.ReleaseMutex(); $other.Dispose()
} finally { $handle.ReleaseMutex(); $handle.Dispose() }
# Exercise the actual production loop, with only the API and sleep replaced.
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Start-DocMindUEncryptorHostWorker.ps1'), [ref]$tokens, [ref]$errors)
Assert-True ($errors.Count -eq 0) 'Worker parse failed.'
$loop = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.DoWhileStatementAst] }, $true))[-1].Extent.Text
foreach ($role in @('ingest', 'preview')) {
    $Purpose = $role; $Once = $true; $ClaimJobId = ''; $ClaimSourceId = ''; $SkipRetries = $false; $allowedClaimFormats = @()
    $config = [pscustomobject]@{ worker_id = 'synthetic' }; $claimLeaseSeconds = 300; $claimPath = '/ingest'; $previewClaimPath = '/preview'
    $lastReceiptReplayAt = [DateTimeOffset]::UtcNow.AddDays(-1); $cleanupReceiptReplaySeconds = 60
    $workRoot = 'synthetic-work'; $receiptRoot = 'synthetic-receipts'; $lastFencingTokenByJob = @{}; $PollSeconds = 5
    $script:claimed = ''; $script:replayed = ''
    function Send-CleanupReceipt { param($Receipt, $Purpose) $script:replayed = $Purpose }
    function Invoke-DocMindCleanupReceiptReplay { param($ReceiptRoot, $WorkRoot, $Sender) & $Sender $null; return [pscustomobject]@{ pending = 0; acknowledged = 1 } }
    function Invoke-SignedJsonRequest { param($Method, $RelativeUri, $Body) $script:claimed = $RelativeUri; return [pscustomobject]@{ Body = [Text.Encoding]::UTF8.GetBytes('{"job":null}') } }
    Invoke-Expression $loop
    Assert-True ($script:claimed -eq ('/' + $role) -and $script:replayed -eq $role) 'Worker claimed or replayed another role.'
}
'PASS: role paths, process singleton, role-only claim and cleanup replay.'
