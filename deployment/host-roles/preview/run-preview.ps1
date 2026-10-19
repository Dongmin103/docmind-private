$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Set-DocMindPreviewWorkerIsolation.ps1'), [ref]$tokens, [ref]$errors)
$node = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Wait-DocMindIsolationApi'}, $false)
Invoke-Expression $node.Extent.Text
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'preview.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Wait-DocMindIsolationApi -Manifest $config
& (Join-Path $PSScriptRoot 'Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $PSScriptRoot 'preview.json') -Purpose preview  -TimingRoot (Join-Path $PSScriptRoot 'timing-preview') *>&1 | Out-File -LiteralPath (Join-Path $PSScriptRoot 'preview.log') -Append -Encoding UTF8