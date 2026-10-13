[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$path = Join-Path $PSScriptRoot 'Set-DocMindPreviewWorkerIsolation.ps1'
if (-not (Test-Path -LiteralPath $path)) { throw 'Deployment script missing.' }
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Deployment script parse failed.' }
foreach ($node in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}, $false)) { Invoke-Expression $node.Extent.Text }
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
$script:DocMindIsolationToolRoot = $PSScriptRoot
$testBase = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/isolation-deployment-tests'))
$testRoot = Join-Path $testBase ([Guid]::NewGuid().ToString('n'))
[IO.Directory]::CreateDirectory((Join-Path $testRoot 'legacy/windows')) | Out-Null
try {
    $script:fixtureLauncher = Join-Path $testRoot 'legacy/run-worker.ps1'
    [IO.File]::WriteAllText($script:fixtureLauncher, "`$release = 'fixture'; & (Join-Path `$release 'windows\Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path `$release 'host-worker.json') -ClaimSourceId synthetic-source -SkipRetries")
    $source = Join-Path $testRoot 'source'; [IO.Directory]::CreateDirectory($source) | Out-Null
    $config = @{ worker_id='synthetic-worker'; api_base_uri='http://127.0.0.1:1'; preview_enabled=$true; work_root=(Join-Path $testRoot 'work'); cleanup_receipt_root=(Join-Path $testRoot 'receipts'); require_bitlocker=$false; backup_exclusion_acknowledged=$true; sources=@(@{source_id='synthetic-source';root=$source}) }
    [IO.File]::WriteAllText((Join-Path $testRoot 'legacy/host-worker.json'), ($config | ConvertTo-Json -Depth 5))
    foreach ($name in @('Start-DocMindUEncryptorHostWorker.ps1','DocMindUEncryptorHostWorker.Common.ps1')) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $testRoot ('legacy/windows/'+$name)) }
    function Get-ScheduledTask { param($TaskName) if ($TaskName) { return [pscustomobject]@{State='Disabled';Settings=[pscustomobject]@{Enabled=$false};Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments=('-File "'+$script:fixtureLauncher+'"')})} }; return @() }
    function Export-ScheduledTask { param($TaskName) return '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings><MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy></Settings><Actions><Exec><Command>powershell.exe</Command><Arguments>old</Arguments></Exec></Actions></Task>' }
    function Get-DocMindTaskState { param($Name) return 'Missing' }
    function docker { return @() }
    $manifestFile = Join-Path $testRoot 'prepared/manifest.json'
    $prepared = New-DocMindIsolationManifest -Path $manifestFile -TaskName 'synthetic-old' -ConfigPath '' -MemoryReport '' -FunctionalReport '' -BackupExcluded $true
    Assert-True (-not $prepared.original.enabled -and -not $prepared.original.running) 'Disabled original state was changed.'
    Assert-True ((Get-Content -LiteralPath $prepared.workers[0].config -Raw | ConvertFrom-Json).sources[0].source_id -eq 'synthetic-source') 'Prepare broadened source scope.'
    Assert-True ([IO.File]::ReadAllText((Join-Path $testRoot 'prepared/after/run-ingest.ps1')).Contains('-ClaimSourceId synthetic-source -SkipRetries')) 'Prepare dropped ingestion filters.'
    Assert-DocMindIsolationArtifacts -Manifest $prepared
    [IO.File]::AppendAllText($prepared.artifacts[0].path, 'changed')
    $rejected=$false; try { Assert-DocMindIsolationArtifacts -Manifest $prepared } catch { $rejected=$_.Exception.Message -eq 'PREPARED_ARTIFACT_CHANGED' }
    Assert-True $rejected 'Changed prepared artifact was accepted.'
    $prepared.control_root = Join-Path $testRoot 'control'; [IO.Directory]::CreateDirectory($prepared.control_root) | Out-Null
    [IO.File]::WriteAllText((Join-Path $prepared.control_root 'blocked.json'), '{}')
    $rejected=$false; try { Assert-DocMindIsolationQuiescent -Manifest $prepared } catch { $rejected=$_.Exception.Message -eq 'DECRYPT_GATE_BLOCKED' }
    Assert-True $rejected 'Persistent decrypt block did not prevent transition.'
} finally {
    if ([IO.Path]::GetFullPath($testRoot).StartsWith($testBase + '\', [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
$script:events = [Collections.Generic.List[string]]::new()
$script:unsafe = $false; $script:registrationFails = $false; $script:applied = $false
function Assert-DocMindIsolationQuiescent { param($Manifest, [switch]$AllowLegacyReceipts) if ($script:unsafe) { throw 'UNRESOLVED_WORK' } }
function Assert-DocMindIsolationArtifacts { param($Manifest) }
function Enter-DocMindIsolationTransition { param($Manifest) return @() }
function Exit-DocMindIsolationTransition { param($Handles) }
function Invoke-DocMindLegacyReceiptRecovery { param($Manifest) }
function Get-DocMindTaskState { param($Name) return 'Ready' }
function Wait-DocMindIsolationApi { param($Manifest) }
function Assert-DocMindIsolationReleaseGates { param($Manifest) if (-not $Manifest.memory_gate.passed -or -not $Manifest.functional_gate.passed) { throw 'ISOLATION_DEPLOYMENT_GATE_FAILED' } }
function Assert-DocMindNoRoleWorkers { param($Manifest) }
function Initialize-DocMindRoleRoots { param($Worker) }
function Set-DocMindIsolationTask {
    param($Name, $Operation, $Xml)
    $script:events.Add($Name + ':' + $Operation)
    if ($Operation -eq 'register' -and $Name -eq 'new-preview' -and $script:registrationFails) { throw 'REGISTER_FAILED' }
}
function Save-DocMindIsolationManifest { param($Manifest, $Path) }
$manifest = [pscustomobject]@{ phase = 'prepared'; original = [pscustomobject]@{ name='old'; xml='old-xml'; enabled=$true; running=$true }; workers=@([pscustomobject]@{name='new-ingest';xml='ingest'},[pscustomobject]@{name='new-preview';xml='preview'}); memory_gate=[pscustomobject]@{passed=$true}; functional_gate=[pscustomobject]@{passed=$true} }
$script:unsafe = $true
try { Invoke-DocMindIsolationTransition -Action Apply -Manifest $manifest -ManifestPath 'mock' } catch { }
Assert-True ($script:events.Count -eq 0) 'Unsafe Apply mutated scheduler.'
$script:unsafe = $false; $script:registrationFails = $true
try { Invoke-DocMindIsolationTransition -Action Apply -Manifest $manifest -ManifestPath 'mock' } catch { }
Assert-True ($script:events.Contains('old:start')) 'Partial Apply did not restore original running task.'
$lastOldStart = $script:events.LastIndexOf('old:start')
foreach ($name in @('new-ingest','new-preview')) { Assert-True ($script:events.LastIndexOf($name+':stop') -lt $lastOldStart) 'Old worker started before new workers stopped.' }
$script:events.Clear(); $script:registrationFails = $false
Invoke-DocMindIsolationTransition -Action Apply -Manifest $manifest -ManifestPath 'mock'
$count = $script:events.Count
Invoke-DocMindIsolationTransition -Action Apply -Manifest $manifest -ManifestPath 'mock'
Assert-True ($script:events.Count -eq $count) 'Reapply duplicated tasks.'
$script:events.Clear()
Invoke-DocMindIsolationTransition -Action Rollback -Manifest $manifest -ManifestPath 'mock'
$start = $script:events.IndexOf('old:start')
Assert-True ($start -ge 0 -and $script:events.IndexOf('new-ingest:stop') -lt $start -and $script:events.IndexOf('new-preview:stop') -lt $start) 'Rollback ran old worker beside new workers.'
'PASS: unsafe preflight, partial failure recovery, idempotent Apply and exclusive Rollback.'
