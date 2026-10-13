$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Measure-DocMindPreviewIsolation.ps1'),[ref]$tokens,[ref]$errors)
$node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-DocMindMeasuredWorkerRole'},$false)
if (-not $node) { throw 'Worker role sampling helper missing.' }
Invoke-Expression $node.Extent.Text
foreach ($role in @('ingest','preview')) {
    if ((Get-DocMindMeasuredWorkerRole -CommandLine ('powershell.exe -File "C:\fixture\run-'+$role+'.ps1"')) -ne $role) { throw 'Generated launcher omitted from worker memory coverage.' }
}
if ((Get-DocMindMeasuredWorkerRole -CommandLine 'powershell.exe -File "C:\fixture\run-dept2-worker.ps1"') -ne 'mixed') { throw 'Existing worker omitted from memory coverage.' }
if ((Get-DocMindMeasuredWorkerRole -CommandLine 'powershell.exe -File "C:\fixture\run-service.ps1"')) { throw 'Unrelated process included as a worker.' }
'PASS: generated and legacy worker memory coverage.'
