[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
Add-Type -Path @((Join-Path $PSScriptRoot 'DocMindSyncQueue.cs'),(Join-Path $PSScriptRoot 'DocMindUnicodeCaseFold.cs'))
function Assert-True([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Watch-DocMindEncryptedSources.ps1'),[ref]$tokens,[ref]$errors)
Assert-True ($errors.Count -eq 0) 'Watcher parse failed.'
foreach($name in @('Send-Outbox','Freeze-And-Send','Process-Due','Process-SourceChanges')){
    $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false)
    Assert-True ($null -ne $node) ('Missing watcher function: '+$name)
    Invoke-Expression $node.Extent.Text
}
$base=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/sync-throughput-tests'))
$testRoot=Join-Path $base ([Guid]::NewGuid().ToString('n'))
$queue=[DocMind.SyncQueue]::new((Join-Path $testRoot 'queue.sqlite'))
$sessions=@{};$ownerId='owner-test';$config=[pscustomobject]@{worker_id='worker-test'};$changesUri='/changes'
$script:requests=[Collections.Generic.List[object]]::new();$script:deliveryMode='ok';$script:readDelay=0;$script:dirtyDelay=0
$script:roots=@{};$script:badPath=''
function New-TestSession([string]$Id){$sessions[$Id]=[pscustomobject]@{epoch=1L;last_sequence=0L}}
function Send-SignedJson {
    param($Path,$Json)
    $request=$Json|ConvertFrom-Json
    $script:requests.Add($request)
    if($script:dirtyDelay -and $request.items[0].kind -eq 'dirty'){Start-Sleep -Milliseconds $script:dirtyDelay}
    if($script:deliveryMode -eq 'retry'){return [pscustomobject]@{Status=503;Body=[pscustomobject]@{error='UNAVAILABLE'}}}
    if($script:deliveryMode -eq 'fenced'){return [pscustomobject]@{Status=409;Body=[pscustomobject]@{error='SESSION_FENCED'}}}
    $results=@($request.items|ForEach-Object{[pscustomobject]@{observation_id=$_.observation_id;state='INDEX_CURRENT'}})
    return [pscustomobject]@{Status=200;Body=[pscustomobject]@{accepted=$true;epoch=1L;sequence=$request.sequence;items=$results}}
}
function Read-Fingerprint {
    param($Id,$Entry)
    if($Entry.RelativePath -eq $script:badPath){throw 'SOURCE_MISSING'}
    if($script:readDelay){Start-Sleep -Milliseconds $script:readDelay}
    return [pscustomobject]@{Hash=('b'*64);Size=1L;Mtime=1L;Identity=$null;Fingerprint=('a'*64)}
}
try {
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    New-TestSession 'many'
    for($i=0;$i -lt 40;$i++){$null=$queue.EnqueueScan('many',('file-{0:D2}.pdf' -f $i),$now)}
    Assert-True (Process-SourceChanges -Id many) 'Ready source reported no progress.'
    $first=@($script:requests|ForEach-Object{$_.items}|Where-Object{$_.observation_id -like '*-first-*'})
    Assert-True ($first.Count -gt 1 -and $first.Count -le 32) 'Source visit did not process a bounded set of files.'
    while($queue.Due('many',[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(),1).Length){$null=Process-SourceChanges -Id many}
    $first=@($script:requests|ForEach-Object{$_.items}|Where-Object{$_.observation_id -like '*-first-*'})
    Assert-True ($first.Count -eq 40) 'Ready files did not drain across visits.'
    Assert-True (-not (Process-SourceChanges -Id many)) 'Waiting stability interval caused a busy loop.'
    Assert-True ($queue.Count('many') -eq 40) 'Files completed before the ten-second recheck.'
    $entries=$queue.Due('many',$now+60000,250)
    foreach($entry in $entries){$queue.Delay($entry,[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(),0,1)}
    $null=$queue.EnqueueScan('many','fresh.pdf',[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    $before=$script:requests.Count
    $null=Process-SourceChanges -Id many
    $after=@($script:requests|Select-Object -Skip $before|ForEach-Object{$_.items})
    Assert-True (@($after|Where-Object{$_.observation_id -like '*-second-*'}).Count -gt 0) 'Dirty traffic starved second observations.'
    $observations=@($after|Where-Object{$_.kind -ne 'dirty'})
    Assert-True ($observations[0].observation_id -like '*-second-*') 'First observations outranked due rechecks.'

    New-TestSession 'bad';$script:badPath='bad.pdf'
    $null=$queue.EnqueueScan('bad','bad.pdf',$now);$null=$queue.EnqueueScan('bad','good.pdf',$now)
    $before=$script:requests.Count;$null=Process-SourceChanges -Id bad
    Assert-True (@($script:requests|Select-Object -Skip $before|ForEach-Object{$_.items}|Where-Object{$_.relative_path -eq 'good.pdf' -and $_.kind -eq 'upsert'}).Count -eq 1) 'Bad file blocked a healthy file.'
    $script:badPath=''
    foreach($mode in @('retry','fenced')){
        New-TestSession $mode;$script:deliveryMode=$mode
        $null=$queue.EnqueueScan($mode,'file.pdf',$now)
        $before=$script:requests.Count;$null=Process-SourceChanges -Id $mode
        Assert-True ($script:requests.Count -eq $before+1 -and $null -ne $queue.Outbox($mode)) 'Unacknowledged outbox did not stop source visit.'
        if($mode -eq 'retry'){Assert-True (-not (Process-SourceChanges -Id $mode)) 'Retry wait caused a busy loop.'}
        else{Assert-True (-not $sessions.ContainsKey($mode)) 'Fenced source kept its session.'}
    }
    $script:deliveryMode='ok';New-TestSession 'budget';$script:readDelay=600
    for($i=0;$i -lt 4;$i++){$null=$queue.EnqueueScan('budget',("file-$i.pdf"),$now)}
    $before=$script:requests.Count;$null=Process-SourceChanges -Id budget
    $budgetFirst=@($script:requests|Select-Object -Skip $before|ForEach-Object{$_.items}|Where-Object{$_.kind -eq 'upsert'})
    Assert-True ($budgetFirst.Count -ge 1 -and $budgetFirst.Count -le 2) 'Source visit ignored one-second budget.'
    $script:readDelay=0;New-TestSession 'slow-dirty';$script:dirtyDelay=1100
    $null=$queue.EnqueueScan('slow-dirty','waiting.pdf',$now)
    $before=$script:requests.Count;$null=Process-SourceChanges -Id slow-dirty
    Assert-True (@($script:requests|Select-Object -Skip $before|ForEach-Object{$_.items}|Where-Object{$_.kind -eq 'upsert'}).Count -eq 1) 'Slow dirty ACK starved stability observations.'
    $script:dirtyDelay=0;New-TestSession 'stale-dirty'
    $null=$queue.EnqueueScan('stale-dirty','stable.pdf',$now)
    $null=Process-SourceChanges -Id stale-dirty
    $stable=$queue.Due('stale-dirty',$now+60000,1)[0];$queue.Delay($stable,$now,0,1)
    $null=$queue.EnqueueScan('stale-dirty','changing.pdf',$now)
    $originalFreeze=(Get-Command Freeze-And-Send).ScriptBlock
    function Freeze-And-Send {
        param($Id,$Entries,$Items)
        if($Id -eq 'stale-dirty' -and $Items[0].kind -eq 'dirty'){
            $null=$queue.Enqueue($Id,'changing.pdf','upsert',$null,[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        }
        & $originalFreeze -Id $Id -Entries $Entries -Items $Items
    }
    $before=$script:requests.Count;$null=Process-SourceChanges -Id stale-dirty
    Assert-True (@($script:requests|Select-Object -Skip $before|ForEach-Object{$_.items}|Where-Object{$_.observation_id -like '*-second-*'}).Count -eq 1) 'Stale dirty snapshot starved an unrelated due recheck.'

    function Test-SourceBackoff {
        $loop=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.DoWhileStatementAst]},$true))[-1].Extent.Text
        $roots=@{broken='unused';healthy='unused'};$scans=@{};$sessions=@{healthy=[pscustomobject]@{epoch=1}}
        $renewed=@{healthy=[DateTimeOffset]::UtcNow.AddDays(1)};$watchers=@{broken=[pscustomobject]@{Faulted=$false};healthy=[pscustomobject]@{Faulted=$false}}
        $scanRetryAt=@{};$sourceRetryAt=@{};$lastMidnight='';$Once=$false;$PollSeconds=2
        $script:sessionAttempts=0;$script:healthyVisits=0
        function Open-Session {param($Id,[switch]$Renew) $script:sessionAttempts++;throw 'SESSION_UNAVAILABLE'}
        function Process-Scope {param($Id)}
        function Process-SourceChanges {param($Id) $script:healthyVisits++;if($script:healthyVisits -ge 20){Set-Variable -Name Once -Value $true -Scope 1};return $true}
        Invoke-Expression $loop
        Assert-True ($script:healthyVisits -eq 20 -and $script:sessionAttempts -eq 1) 'Busy healthy source bypassed failed-source retry backoff.'
    }
    Test-SourceBackoff
    'PASS: bounded source visits, due recheck priority, dirty fairness, ten-second stability, bad-file isolation, retry/fencing and time budget.'
} finally {
    $queue.Dispose()
    $resolved=[IO.Path]::GetFullPath($testRoot)
    if(-not $resolved.StartsWith($base+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe throughput-test cleanup.'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
