try {
$ErrorActionPreference='Stop'
$root='C:/DocMindDev/docmind/.local/gte-deploy-20261012'
$python='C:/DocMindDev/docmind/.local/gte-runtime/python/python.exe'
@{python_exists=(Test-Path -LiteralPath $python);root_exists=(Test-Path -LiteralPath $root);identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;python=$python} | ConvertTo-Json | Set-Content -LiteralPath "$root/launcher-context.json"
$p=Start-Process -FilePath $python -ArgumentList @('-u',"$root/runtime.py") -WindowStyle Hidden -RedirectStandardOutput "$root/service.stdout.log" -RedirectStandardError "$root/service.stderr.log" -PassThru -Wait
exit $p.ExitCode

} catch { $_.Exception.Message | Set-Content -LiteralPath 'C:/DocMindDev/docmind/.local/gte-deploy-20261012/launcher-error.log'; exit 1 }