$ErrorActionPreference='Stop'
$release='C:\DocMindDev\docmind\.local\folder-auto-sync-20261016\dept2-watcher-release'
$log=Join-Path $release 'watcher.log'
try{& (Join-Path $release 'windows/Watch-DocMindEncryptedSources.ps1') -ConfigPath (Join-Path $release 'host-worker.private.json') -SourceId dept-2-e2e *>> $log}catch{Add-Content -LiteralPath $log -Value ('WATCHER_PROCESS_EXIT '+[DateTimeOffset]::UtcNow.ToString('o')+' '+$_.Exception.GetType().Name);throw}
