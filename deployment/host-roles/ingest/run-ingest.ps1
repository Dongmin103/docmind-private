$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Set-DocMindPreviewWorkerIsolation.ps1'), [ref]$tokens, [ref]$errors)
$node = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Wait-DocMindIsolationApi'}, $false)
Invoke-Expression $node.Extent.Text
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'ingest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Wait-DocMindIsolationApi -Manifest $config
& (Join-Path $PSScriptRoot 'Start-DocMindIngestionPool.ps1') -ConfigPath (Join-Path $PSScriptRoot 'ingest.json') -Concurrency 4 -StopFile (Join-Path $PSScriptRoot 'ingest.stop') -ClaimSourceId dept-2-e2e -SkipRetries -TimingRoot (Join-Path $PSScriptRoot 'timing-ingest') *>&1 | Out-File -LiteralPath (Join-Path $PSScriptRoot 'ingest.log') -Append -Encoding UTF8