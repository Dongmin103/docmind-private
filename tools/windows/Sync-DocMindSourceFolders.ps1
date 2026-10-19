[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindSyncTransport.ps1')
Add-Type -Path @((Join-Path $PSScriptRoot 'DocMindSyncQueue.cs'),(Join-Path $PSScriptRoot 'DocMindUnicodeCaseFold.cs'))
$config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8|ConvertFrom-Json
$base=[Uri]::new([string]$config.api_base_uri)
if(-not $base.IsLoopback -or $base.UserInfo -or $base.Scheme -notin @('http','https')){throw 'HOST_API_NOT_LOOPBACK'}
$secret=Read-DocMindSharedSecret -LiteralPath ([IO.Path]::GetFullPath([string]$config.shared_secret_file))
$stateRoot=[IO.Path]::GetFullPath([string]$config.sync_state_root)
$controls=Join-Path $stateRoot 'folder-owners'
$nonces=@{};$states=@{};$publishedHeads=@{}
$mutex=[Threading.Mutex]::new($false,'Global\DocMind-FolderSync')
$owns=$false;$queue=$null
$helperIdentity=Join-Path $stateRoot 'folder-helper.json'
$endpoint='/api/v1/cloud-sync/host-worker/folders'
function Now-Milliseconds { return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
function Assert-Owner($State) {
    $process=Get-Process -Id ([int]$State.Control.pid) -ErrorAction SilentlyContinue
    if($null -eq $process -or $process.StartTime.ToUniversalTime().Ticks -ne [long]$State.Control.started_ticks -or -not (Test-Path -LiteralPath $State.ControlPath)){throw 'FOLDER_OWNER_STOPPED'}
}
function Folder-Request($State,[string]$Action,[hashtable]$Extra) {
    Assert-Owner $State
    # Hashing can occupy the parent for a long time; metadata delivery keeps
    # the shared owner lease alive without changing the file sequence.
    if([DateTimeOffset]::UtcNow -gt $State.Renewed.AddSeconds(30)){
        $renew=@{protocol_version=2;action='renew';source_id=$State.Id;worker_id=[string]$config.worker_id;owner_id=[string]$State.Control.owner_id;epoch=[long]$State.Epoch}
        $reply=Send-SignedJson -Path '/api/v1/cloud-sync/host-worker/changes/session' -Json ($renew|ConvertTo-Json -Compress)
        if($reply.Status -ne 200){throw ('FOLDER_SESSION_'+[string]$reply.Body.error)}
        $State.Renewed=[DateTimeOffset]::UtcNow
    }
    $body=@{protocol_version=1;source_id=$State.Id;worker_id=[string]$config.worker_id;owner_id=[string]$State.Control.owner_id;epoch=[long]$State.Epoch;action=$Action}
    foreach($key in $Extra.Keys){$body[$key]=$Extra[$key]}
    return $body
}
function Deliver-Outbox($State) {
    $box=$queue.FolderOutbox($State.Id)
    if($null -eq $box){return}
    if($box.Epoch -ne $State.Epoch){$queue.AcknowledgeFolder($State.Id,$box.RequestId);return}
    if($box.NextAttemptAt -gt (Now-Milliseconds)){throw 'FOLDER_RETRY_NOT_DUE'}
    try {
        Assert-Owner $State
        $reply=Send-SignedJson -Path $endpoint -Json $box.Payload
        if($reply.Status -ne 200){throw ('FOLDER_HTTP_'+$reply.Status+'_'+[string]$reply.Body.error)}
        if(-not $reply.Body.accepted -or [long]$reply.Body.epoch -ne $box.Epoch -or [long]$reply.Body.sequence -ne $box.Sequence){throw 'FOLDER_ACK_MISMATCH'}
        $queue.AcknowledgeFolder($State.Id,$box.RequestId)
        $State.Sequence=[long]$box.Sequence
    } catch {
        $queue.RetryFolder($State.Id,$box.RequestId,(Now-Milliseconds));throw
    }
}
function Send-Mutation($State,[string]$Action,[hashtable]$Extra) {
    Deliver-Outbox $State
    $body=Folder-Request $State $Action $Extra
    $sequence=[long]$State.Sequence+1
    $body.sequence=$sequence;$body.request_id='folder-'+[Guid]::NewGuid().ToString('n')
    if(-not $queue.FreezeFolder($State.Id,$State.Epoch,$sequence,$body.request_id,($body|ConvertTo-Json -Depth 10 -Compress))){throw 'FOLDER_OUTBOX_BUSY'}
    Deliver-Outbox $State
}
function Read-Page($State,[string]$Action,[hashtable]$Extra) {
    $body=Folder-Request $State $Action $Extra
    $reply=Send-SignedJson -Path $endpoint -Json ($body|ConvertTo-Json -Depth 8 -Compress)
    if($reply.Status -ne 200){throw ('FOLDER_HTTP_'+$reply.Status+'_'+[string]$reply.Body.error)}
    return $reply.Body
}
function Resolve-FolderPath($State,[string]$Relative) {
    if(-not $Relative){return $State.Root}
    return Resolve-DocMindSourceCandidate -Root $State.Root -RelativePath $Relative
}
function Confirm-FolderAbsence($State,[string]$Relative) {
    $candidate=Resolve-FolderPath $State $Relative
    # Confirm access to the root on every absence observation, including when
    # a descendant's cached existence information says it is absent.
    Assert-DocMindNoReparsePoint -LiteralPath $State.Root -Boundary $State.Root -Name 'Folder source root'
    $probe=[IO.Directory]::EnumerateFileSystemEntries($State.Root).GetEnumerator()
    try{[void]$probe.MoveNext()}finally{if($probe -is [IDisposable]){$probe.Dispose()}}
    $attributes=$null
    try{$attributes=[IO.File]::GetAttributes($candidate)}catch [IO.FileNotFoundException]{}catch [IO.DirectoryNotFoundException]{}
    if($null -ne $attributes -and ($attributes -band [IO.FileAttributes]::Directory)){return $false}
    if($null -ne $attributes){
        Assert-DocMindNoReparsePoint -LiteralPath $candidate -Boundary $State.Root -Name 'Folder replacement'
        return $true
    }
    return Test-DocMindConfirmedSourceFileAbsence -Root $State.Root -RelativePath $Relative
}
function Queue-DocumentAbsences($State,[string]$Relative) {
    $cursor=$null;$waiting=$false
    do {
        $page=Read-Page $State 'documents' @{relative_path=$Relative;cursor=$cursor;limit=250}
        foreach($item in @($page.candidates)){
            if($queue.HasPendingMove($State.Id,[string]$item.relative_path)){$waiting=$true;continue}
            # Discovery must not reset a newer event or in-flight hash. A file
            # still present will be checked by its existing file worker path.
            $null=$queue.EnqueueDiscovered($State.Id,[string]$item.relative_path,$null,(Now-Milliseconds))
            if(Test-DocMindConfirmedSourceFileAbsence -Root $State.Root -RelativePath ([string]$item.relative_path)){
                $null=$queue.Enqueue($State.Id,[string]$item.relative_path,'delete',$null,(Now-Milliseconds))
            }
        }
        $cursor=$page.next_cursor
    }while($null -ne $cursor)
    return (-not $waiting)
}
function Flush-Folders($State,$Batch,[string]$Snapshot) {
    if($Batch.Count -eq 0){return}
    $extra=@{items=@($Batch.ToArray())}
    if($Snapshot){$extra.snapshot_id=$Snapshot}
    Send-Mutation $State 'batch' $extra
    $Batch.Clear()
}
function Publish-PendingHeads {
    # A large subtree walk yields metadata delivery to newly captured events,
    # including other sources. File discovery continues in its original scope.
    foreach($other in @($states.Values)){
        if(-not $other.Initialized){continue}
        foreach($head in @($queue.FolderHeads($other.Id,(Now-Milliseconds),128))){
            if($head.Kind -eq 'recovery'){continue}
            $key=$other.Id+':'+$other.Epoch+':'+$head.RelativePath
            if($publishedHeads.ContainsKey($key) -and [long]$publishedHeads[$key] -ge $head.Generation){continue}
            try {
                if($head.Kind -eq 'delete'){
                    if(-not (Confirm-FolderAbsence $other ([string]$head.RelativePath))){continue}
                    if($head.AbsenceCount -eq 0){$queue.DelayFolderScope($head,((Now-Milliseconds)+2000),1);continue}
                    $item=@{kind='delete';relative_path=[string]$head.RelativePath;generation=[long]$head.Generation;root_access_confirmed=$true;absence_observations=2}
                }else{
                    $path=Resolve-FolderPath $other ([string]$head.RelativePath)
                    if(-not [IO.Directory]::Exists($path)){continue}
                    Assert-DocMindNoReparsePoint -LiteralPath $path -Boundary $other.Root -Name 'Priority folder head'
                    $item=@{kind='upsert';relative_path=[string]$head.RelativePath;generation=[long]$head.Generation}
                }
                Send-Mutation $other 'batch' @{items=@($item)}
                $publishedHeads[$key]=[long]$head.Generation
            }catch{Write-Warning ('FOLDER_HEAD_RETRY '+$other.Id+' '+$_.Exception.GetType().Name)}
        }
    }
}
function Walk-Folders($State,$Scope) {
    $relative=[string]$Scope.RelativePath
    $start=Resolve-FolderPath $State $relative
    Assert-DocMindNoReparsePoint -LiteralPath $start -Boundary $State.Root -Name 'Observed folder'
    if(-not [IO.Directory]::Exists($start)){throw 'FOLDER_NOT_PRESENT'}
    $snapshot='snapshot-'+[Guid]::NewGuid().ToString('n')
    $movePaths=@{}
    if($Scope.Kind -eq 'move'){
        foreach($origin in @($queue.FolderMoveOrigins($State.Id,$relative))){
            $cursor=$null
            do {
                $page=Read-Page $State 'documents' @{relative_path=[string]$origin;cursor=$cursor;limit=250}
                foreach($item in @($page.candidates)){
                    if($item.PSObject.Properties.Name -contains 'host_file_identity' -and $item.host_file_identity){$movePaths[[string]$item.host_file_identity]=[string]$item.relative_path}
                }
                $cursor=$page.next_cursor
            }while($null -ne $cursor)
        }
    }
    Send-Mutation $State 'snapshot_begin' @{snapshot_id=$snapshot;relative_path=$relative}
    $batch=[Collections.Generic.List[object]]::new()
    $stack=[Collections.Generic.Stack[string]]::new();$stack.Push($relative)
    $complete=$false
    $slice=[Diagnostics.Stopwatch]::StartNew()
    try {
        while($stack.Count){
            $current=$stack.Pop();$directory=Resolve-FolderPath $State $current
            Assert-Owner $State
            Assert-DocMindNoReparsePoint -LiteralPath $directory -Boundary $State.Root -Name 'Observed directory'
            if($current -and $current -eq $relative){$queue.RememberFolder($State.Id,$current,$true);$batch.Add(@{kind='upsert';relative_path=$current;generation=[long]$Scope.Generation})}
            # Publish each parent before any costly file discovery in it.
            Flush-Folders $State $batch $snapshot
            foreach($child in [IO.Directory]::EnumerateDirectories($directory)){
                Assert-DocMindNoReparsePoint -LiteralPath $child -Boundary $State.Root -Name 'Observed child'
                $path=$child.Substring($State.Root.Length+1).Replace('\','/')
                $queue.RememberFolder($State.Id,$path,$true)
                $batch.Add(@{kind='upsert';relative_path=$path;generation=[long]$Scope.Generation})
                $stack.Push($path)
                if($batch.Count -ge 100){Flush-Folders $State $batch $snapshot}
                if($slice.ElapsedMilliseconds -ge 250){Flush-Folders $State $batch $snapshot;Publish-PendingHeads;$slice.Restart()}
            }
            Flush-Folders $State $batch $snapshot
            if($Scope.Kind -ne 'recovery'){
                foreach($file in [IO.Directory]::EnumerateFiles($directory)){
                    Assert-DocMindNoReparsePoint -LiteralPath $file -Boundary $State.Root -Name 'Discovered file'
                    $path=$file.Substring($State.Root.Length+1).Replace('\','/')
                    $old=$null
                    if($Scope.Kind -eq 'move' -and $Scope.OldRelativePath){
                        $identity=Get-DocMindHostFileIdentity -LiteralPath $file
                        $identityKey=[string]$config.worker_id+':'+$identity
                        if($identity -and $movePaths.ContainsKey($identityKey)){$old=$movePaths[$identityKey]}
                        foreach($origin in @($queue.FolderMoveOrigins($State.Id,$relative))){
                            $intermediate=[string]$origin+$path.Substring($relative.Length)
                            $revision=$queue.PendingGeneration($State.Id,$intermediate)
                            if($intermediate -ne $path -and (Test-DocMindConfirmedSourceFileAbsence -Root $State.Root -RelativePath $intermediate)){
                                if(-not $queue.RetireIntermediateFile($State.Id,$intermediate,$revision)){throw 'FOLDER_MOVE_INFLIGHT'}
                            }
                        }
                    }
                    $null=$queue.EnqueueDiscovered($State.Id,$path,$old,(Now-Milliseconds))
                    if($slice.ElapsedMilliseconds -ge 250){Publish-PendingHeads;$slice.Restart()}
                }
            }
        }
        # A root or subtree becoming inaccessible invalidates the snapshot.
        $probe=[IO.Directory]::EnumerateFileSystemEntries($start).GetEnumerator()
        try{[void]$probe.MoveNext()}finally{if($probe -is [IDisposable]){$probe.Dispose()}}
        Send-Mutation $State 'snapshot_end' @{snapshot_id=$snapshot;complete=$true}
        $complete=$true
    } finally {
        # Interrupted walks intentionally have no authoritative completion.
        # In particular, an ACK-lost completion must be replayed unchanged.
    }
    $cursor=$null
    do {
        $page=Read-Page $State 'candidates' @{snapshot_id=$snapshot;cursor=$cursor;limit=250}
        foreach($item in @($page.candidates)){$queue.EnqueueFolderScope($State.Id,[string]$item.relative_path,$null,'delete',(Now-Milliseconds))}
        $cursor=$page.next_cursor
    }while($null -ne $cursor)
    if($Scope.Kind -eq 'move' -and $Scope.OldRelativePath){$queue.EnqueueFolderScope($State.Id,[string]$Scope.OldRelativePath,$null,'delete',(Now-Milliseconds))}
    if($relative){
        # A directory may replace a previously indexed file at the same path.
        # Queueing a file absence does not authorize a folder/index bulk delete.
        $null=$queue.Enqueue($State.Id,$relative,'delete',$null,(Now-Milliseconds))
    }
}
try {
    try{$owns=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owns=$true}
    if(-not $owns){exit 0}
    [IO.File]::WriteAllText($helperIdentity,(@{pid=$PID;started_ticks=(Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks}|ConvertTo-Json -Compress))
    $queue=[DocMind.SyncQueue]::new((Join-Path $stateRoot 'sync.sqlite'))
    while($true){
        $active=@{}
        foreach($file in @(Get-ChildItem -LiteralPath $controls -Filter '*.json' -ErrorAction SilentlyContinue)){
            try {
                $control=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8|ConvertFrom-Json
                foreach($source in @($control.sources)){
                    $id=[string]$source.source_id
                    $configured=@($config.sources|Where-Object{[string]$_.source_id -eq $id})
                    if($configured.Count -ne 1){continue}
                    $state=[pscustomobject]@{Id=$id;Root=[IO.Path]::GetFullPath([string]$configured[0].root).TrimEnd('\','/');Control=$control;ControlPath=$file.FullName;Epoch=[long]$source.epoch;Sequence=0L;Initialized=$false;Renewed=[DateTimeOffset]::MinValue;RecoveryAt=[DateTimeOffset]::UtcNow.AddMinutes(5)}
                    Assert-Owner $state
                    $active[$id]=$state
                }
            }catch{}
        }
        if(-not $active.Count){break}
        foreach($id in @($active.Keys)){
            try {
                $incoming=$active[$id]
                if(-not $states.ContainsKey($id) -or -not $states[$id].Initialized -or $states[$id].Epoch -ne $incoming.Epoch -or $states[$id].Control.owner_id -ne $incoming.Control.owner_id){
                    $states[$id]=$incoming
                    Deliver-Outbox $incoming
                    $status=Read-Page $incoming 'status' @{}
                    $incoming.Sequence=[long]$status.last_sequence
                    $queue.EnqueueFolderScope($id,'',$null,'recovery',(Now-Milliseconds))
                    $incoming.Initialized=$true
                }
            }catch{Write-Warning ("FOLDER_SESSION_RETRY "+$id+" "+$_.Exception.GetType().Name);$states.Remove($id)}
        }
        foreach($id in @($active.Keys)){
            if(-not $states.ContainsKey($id) -or -not $states[$id].Initialized){continue}
            try {
                $state=$states[$id]
                Deliver-Outbox $state
                $null=Folder-Request $state 'status' @{}
                if([DateTimeOffset]::UtcNow -gt $state.RecoveryAt){$queue.EnqueueFolderScope($id,'',$null,'recovery',(Now-Milliseconds));$state.RecoveryAt=[DateTimeOffset]::UtcNow.AddMinutes(5)}
                foreach($scope in @($queue.FolderScopes($id,(Now-Milliseconds),8))){
                    try {
                    if($scope.Kind -eq 'delete'){
                        if(-not (Confirm-FolderAbsence $state ([string]$scope.RelativePath))){
                            $queue.EnqueueFolderScope($id,[string]$scope.RelativePath,$null,'create',(Now-Milliseconds));continue
                        }
                        if($scope.AbsenceCount -eq 0){$queue.DelayFolderScope($scope,((Now-Milliseconds)+2000),1);continue}
                        Send-Mutation $state 'batch' @{items=@(@{kind='delete';relative_path=[string]$scope.RelativePath;generation=[long]$scope.Generation;root_access_confirmed=$true;absence_observations=2})}
                        $queue.RememberFolder($id,[string]$scope.RelativePath,$false)
                        if(-not (Queue-DocumentAbsences $state ([string]$scope.RelativePath))){$queue.DelayFolderScope($scope,((Now-Milliseconds)+2000),1);continue}
                    }else{
                        try{Walk-Folders $state $scope}catch{
                            if($_.Exception.Message -eq 'FOLDER_NOT_PRESENT'){$queue.EnqueueFolderScope($id,[string]$scope.RelativePath,$null,'delete',(Now-Milliseconds));continue};throw
                        }
                    }
                    $queue.CompleteFolderScope($scope)
                    } catch {
                        # One inaccessible subtree must not monopolize a
                        # source's metadata loop or grant deletion authority.
                        $queue.DelayFolderScope($scope,((Now-Milliseconds)+5000),$scope.AbsenceCount)
                        Write-Warning ('FOLDER_SCOPE_RETRY '+$id+' '+$_.Exception.GetType().Name)
                    }
                }
            }catch{
                Write-Warning ('FOLDER_SYNC_RETRY '+$id+' '+$_.Exception.GetType().Name)
                if($_.Exception.Message -match 'SESSION_(FENCED|EXPIRED)|FOLDER_OWNER_STOPPED'){$states.Remove($id)}
            }
        }
        foreach($id in @($states.Keys)){if(-not $active.ContainsKey($id)){$states.Remove($id)}}
        Start-Sleep -Milliseconds 500
    }
}finally{
    if($null -ne $queue){$queue.Dispose()}
    if($owns -and [IO.File]::Exists($helperIdentity)){[IO.File]::Delete($helperIdentity)}
    if($owns){$mutex.ReleaseMutex()};$mutex.Dispose()
}
