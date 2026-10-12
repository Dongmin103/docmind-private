[CmdletBinding()]
param([switch]$Baseline,[switch]$SchedulerBenchmark)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$base=Join-Path $repoRoot '.local/scan-throughput-tests'
$testRoot=Join-Path $base ([Guid]::NewGuid().ToString('n'))
$testSourceId='test-'+[Guid]::NewGuid().ToString('n')
$source=Join-Path $testRoot 'source';$state=Join-Path $testRoot 'state'
[IO.Directory]::CreateDirectory($source)|Out-Null
$names=if($SchedulerBenchmark){@(0..11|ForEach-Object{'file-{0:D2}.pdf' -f $_})}else{@('first.pdf','second.docx','third.pptx','inventory-only.bin')}
foreach($name in $names){[IO.File]::WriteAllText((Join-Path $source $name),'synthetic encrypted content')}
$secret=Join-Path $testRoot 'secret.bin';[IO.File]::WriteAllBytes($secret,[byte[]](0..31))
$listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0);$listener.Start();$port=([Net.IPEndPoint]$listener.LocalEndpoint).Port;$listener.Stop()
$log=Join-Path $testRoot 'requests.jsonl';$configPath=Join-Path $testRoot 'config.json'
@{worker_id='test-worker';api_base_uri="http://127.0.0.1:$port";key_id='test-key';shared_secret_file=$secret;sync_state_root=$state;sources=@(@{source_id=$testSourceId;root=$source})}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $configPath -Encoding UTF8
$scriptRoot=$PSScriptRoot
if($Baseline){
    $scriptRoot=Join-Path $testRoot 'baseline';[IO.Directory]::CreateDirectory($scriptRoot)|Out-Null
    foreach($name in @('Watch-DocMindEncryptedSources.ps1','Invoke-DocMindSourceReconciliation.ps1','DocMindUEncryptorHostWorker.Common.ps1','DocMindSyncQueue.cs','DocMindSyncCollector.cs','DocMindUnicodeCaseFold.cs')){
        $content=& git -C $repoRoot show "8a3bbc9:tools/windows/$name"
        if($LASTEXITCODE -ne 0){throw 'Could not extract baseline scripts.'}
        [IO.File]::WriteAllText((Join-Path $scriptRoot $name),($content -join "`n"),[Text.UTF8Encoding]::new($true))
    }
}
$python='C:\Users\uplex\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if(-not (Test-Path -LiteralPath $python)){$python=(Get-Command python -ErrorAction Stop).Source}
$shell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$server=$null;$watcher=$null;$scan=$null
try {
    if($SchedulerBenchmark){
        Add-Type -Path @((Join-Path $PSScriptRoot 'DocMindSyncQueue.cs'),(Join-Path $PSScriptRoot 'DocMindUnicodeCaseFold.cs'))
        $seed=[DocMind.SyncQueue]::new((Join-Path $state 'sync.sqlite'))
        try{foreach($name in $names){$null=$seed.Enqueue($testSourceId,$name,'upsert',$null,[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-121000)}}finally{$seed.Dispose()}
    }
    $server=Start-Process -FilePath $python -ArgumentList @((Join-Path $PSScriptRoot 'test_sync_stub.py'),'--port',$port,'--log',('"'+$log+'"'),'--scan-delay','15') -WindowStyle Hidden -PassThru
    for($i=0;$i -lt 50;$i++){
        try{$client=[Net.Sockets.TcpClient]::new('127.0.0.1',$port);$client.Dispose();break}catch{Start-Sleep -Milliseconds 100}
    }
    $started=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    $watcher=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $scriptRoot 'Watch-DocMindEncryptedSources.ps1')+'"'),'-ConfigPath',('"'+$configPath+'"'),'-SkipStartupScan','-PollSeconds','1') -RedirectStandardOutput (Join-Path $testRoot 'watch.out') -RedirectStandardError (Join-Path $testRoot 'watch.err') -WindowStyle Hidden -PassThru
    $owner=$null;$until=[DateTime]::UtcNow.AddSeconds(10)
    do {
        if(Test-Path -LiteralPath $log){
            $requests=@(Get-Content -LiteralPath $log|ForEach-Object{$_|ConvertFrom-Json})
            $session=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'action'})
            if($session.Count){$owner=[string]$session[0].owner_id;break}
        }
        Start-Sleep -Milliseconds 100
    }while([DateTime]::UtcNow -lt $until)
    Assert-True ($null -ne $owner) 'Watcher session did not start.'
    if($SchedulerBenchmark){
        $until=[DateTime]::UtcNow.AddSeconds(40)
        do{
            $requests=@(Get-Content -LiteralPath $log|ForEach-Object{$_|ConvertFrom-Json})
            $second=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'items'}|Where-Object{@($_.items|Where-Object{$_.observation_id -like '*-second-*'}).Count -gt 0})
            if($second.Count -eq $names.Count){break}
            Start-Sleep -Milliseconds 100
        }while([DateTime]::UtcNow -lt $until)
        Assert-True ($second.Count -eq $names.Count) 'Scheduler did not finish all stable observations.'
        'PASS: scheduler benchmark baseline={0}, files={1}, bytes={2}, poll=1s, second-observations-complete={3:N2}s.' -f [bool]$Baseline,$names.Count,($names.Count*27),($second[-1]._received_at-$started)
        return
    }
    $started=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    $scan=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $scriptRoot 'Invoke-DocMindSourceReconciliation.ps1')+'"'),'-ConfigPath',('"'+$configPath+'"'),'-SourceId',$testSourceId,'-OwnerId',$owner,'-Epoch','1','-BatchSize','1') -RedirectStandardOutput (Join-Path $testRoot 'scan.out') -RedirectStandardError (Join-Path $testRoot 'scan.err') -WindowStyle Hidden -PassThru
    [void]$scan.Handle
    Assert-True ($scan.WaitForExit(25000)) 'Synthetic scan exceeded its deadline.'
    $scan.Refresh()
    Assert-True ($scan.ExitCode -eq 0) ('Synthetic scan failed: '+(Get-Content -LiteralPath (Join-Path $testRoot 'scan.err') -Raw))
    $requests=@(Get-Content -LiteralPath $log|ForEach-Object{$_|ConvertFrom-Json})
    $completed=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'event' -and $_.event -eq 'completed'})
    Assert-True ($completed.Count -eq 1 -and $completed[0].file_count -eq 4) 'Full inventory omitted files.'
    $first=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'items'}|Where-Object{@($_.items|Where-Object{$_.observation_id -like '*-first-*'}).Count -gt 0})
    $second=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'items'}|Where-Object{@($_.items|Where-Object{$_.observation_id -like '*-second-*'}).Count -gt 0})
    Assert-True ($second.Count -gt 0 -and $second[0]._received_at -lt $completed[0]._received_at) 'First scanned file did not pass both observations before scan completion.'
    Assert-True ($second[0]._received_at-$first[0]._received_at -ge 10) 'Scan bypassed ten-second stability interval.'
    'PASS: first scanned file stable before full scan completion; inventory includes unsupported suffix; first={0:N2}s, second={1:N2}s, scan={2:N2}s.' -f ($first[0]._received_at-$started),($second[0]._received_at-$started),($completed[0]._received_at-$started)
    $missingConfig=Join-Path $testRoot 'missing-config.json'
    $missing=Get-Content -LiteralPath $configPath -Raw|ConvertFrom-Json;$missing.sources[0].root=Join-Path $testRoot 'missing-root'
    $missing|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $missingConfig -Encoding UTF8
    $scan.Dispose()
    $scan=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $scriptRoot 'Invoke-DocMindSourceReconciliation.ps1')+'"'),'-ConfigPath',('"'+$missingConfig+'"'),'-OwnerId',$owner,'-Epoch','1') -RedirectStandardOutput (Join-Path $testRoot 'scan.out') -RedirectStandardError (Join-Path $testRoot 'scan.err') -WindowStyle Hidden -PassThru
    [void]$scan.Handle;Assert-True ($scan.WaitForExit(10000)) 'Missing-root scan exceeded deadline.';$scan.Refresh()
    Assert-True ($scan.ExitCode -ne 0) 'Missing-root scan was reported successful.'
    $requests=@(Get-Content -LiteralPath $log|ForEach-Object{$_|ConvertFrom-Json})
    $failed=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'event' -and $_.event -eq 'failed'})
    Assert-True ($failed.Count -eq 1 -and -not $failed[0].complete -and -not $failed[0].root_access_confirmed) 'Failed scan retained deletion authority.'
    $failedRequests=@($requests|Where-Object{$_.PSObject.Properties.Name -contains 'scan_id' -and $_.scan_id -eq $failed[0].scan_id})
    Assert-True (@($failedRequests|Where-Object{$_.PSObject.Properties.Name -contains 'event' -and $_.event -eq 'completed'}).Count -eq 0) 'Failed scan sent a completion event.'
    Assert-True (@($failedRequests|Where-Object{$_._path -like '*/candidates'}).Count -eq 0) 'Failed scan requested deletion candidates.'
    'PASS: inaccessible scan reports failure without completion or deletion candidates.'
} catch {
    foreach($name in @('watch.out','watch.err','scan.out','scan.err')){
        $diagnostic=Join-Path $testRoot $name
        if(Test-Path -LiteralPath $diagnostic){Write-Output (Get-Content -LiteralPath $diagnostic -Tail 12)}
    }
    throw
} finally {
    foreach($process in @($scan,$watcher,$server)){
        if($null -ne $process){if(-not $process.HasExited){Stop-Process -Id $process.Id -Force;$null=$process.WaitForExit(5000)};$process.Dispose()}
    }
    $resolved=[IO.Path]::GetFullPath($testRoot)
    if(-not $resolved.StartsWith($base+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe scan-test cleanup.'}
    if(Test-Path -LiteralPath $resolved){Remove-Item -LiteralPath $resolved -Recurse -Force}
}
