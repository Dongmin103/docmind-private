[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -Path @((Join-Path $PSScriptRoot 'DocMindSyncQueue.cs'), (Join-Path $PSScriptRoot 'DocMindUnicodeCaseFold.cs'))
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/sync-queue-tests'))
$testRoot = Join-Path $root ([Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
$dbPath = Join-Path $testRoot 'queue.sqlite'
$queue = $null
try {
    $queue = [DocMind.SyncQueue]::new($dbPath)
    for ($i = 0; $i -lt 100; $i++) {
        $null = $queue.Enqueue('source', 'Reports\A.pdf', 'upsert', $null, [long]($i * 100))
    }
    Assert-True ($queue.Count('source') -eq 1) 'Autosaves did not coalesce by path.'
    Assert-True ($queue.Due('source', 129899, 250).Length -eq 0) 'Hash became due before 120 seconds of quiet.'
    $entry = $queue.Due('source', 129900, 250)[0]
    Assert-True ($entry.Generation -eq 100) 'Event generation did not advance.'
    Assert-True ($entry.RelativePath -eq 'Reports/A.pdf') 'Logical path was not normalized.'
    $null = $queue.Enqueue('source', 'reports/A.PDF', 'upsert', $null, 10000)
    Assert-True ($queue.Count('source') -eq 1) 'Case variants created two pending rows.'
    Assert-True (-not $queue.Freeze('source', 1, 1, 'obsolete', '{}', [DocMind.SyncEntry[]]@($entry))) 'Hash from an obsolete generation entered the outbox.'
    $entry = $queue.Due('source', 130000, 250)[0]
    Assert-True ($queue.Freeze('source', 1, 1, 'request-1', '{"immutable":true}', [DocMind.SyncEntry[]]@($entry))) 'Could not freeze current observation.'
    $queue.Dispose()
    $queue = [DocMind.SyncQueue]::new($dbPath)
    Assert-True ($queue.Outbox('source').Payload -eq '{"immutable":true}') 'Outbox did not survive restart.'
    $queue.Acknowledge('source', 'request-1', [int[]]@(1), 130000)
    Assert-True ($queue.Due('source', 139999, 250).Length -eq 0) 'Second observation ignored 10-second interval.'
    $entry = $queue.Due('source', 140000, 250)[0]
    Assert-True ($entry.StableCount -eq 1) 'First stable observation was lost.'
    Assert-True ($queue.Freeze('source', 1, 2, 'request-2', '{"second":true}', [DocMind.SyncEntry[]]@($entry))) 'Second freeze failed.'
    $null = $queue.Enqueue('source', 'Reports/A.pdf', 'upsert', $null, 140001)
    $queue.Acknowledge('source', 'request-2', [int[]]@(2), 140002)
    Assert-True ($queue.Count('source') -eq 1) 'ACK deleted a newer generation.'
    Assert-True ($null -eq $queue.Outbox('source')) 'ACK did not remove fixed outbox.'
    $null = $queue.Enqueue('other', 'deleted.pdf', 'delete', $null, 1000)
    Assert-True ($queue.Due('other', 1000, 250).Length -eq 1) 'Deletion incorrectly waits 120 seconds.'
    $deleted = $queue.Due('other', 1000, 250)[0]
    $queue.Delay($deleted, 1000, 2000, 1)
    Assert-True ($queue.Due('other', 2999, 250).Length -eq 0) 'Absence recheck happened too early.'
    Assert-True ($queue.Due('other', 3000, 250).Length -eq 1) 'Absence recheck was lost.'
    $null = $queue.Enqueue('other', 'deleted.pdf', 'upsert', $null, 3001)
    Assert-True ($queue.Due('other', 3001, 250).Length -eq 0) 'Reappearing file kept deletion timing.'
    $null = $queue.Enqueue('new', 'fresh.pdf', 'create', $null, 4000)
    $created = $queue.Due('new', 4000, 1)[0]
    Assert-True ($created.InitialRegistration -and $created.Kind -eq 'upsert') 'Created file was not immediately eligible for its first hash.'
    $null = $queue.Enqueue('new', 'fresh.pdf', 'upsert', $null, 4001)
    $created = $queue.Due('new', 4001, 1)[0]
    Assert-True ($created.InitialRegistration -and $created.StableCount -eq 0) 'Copy-time modifications lost new-file timing.'
    $queue.Dispose(); $queue = [DocMind.SyncQueue]::new($dbPath)
    Assert-True ($queue.Due('new', 4001, 1)[0].InitialRegistration) 'New-file timing did not survive restart.'
    $queue.ResetForNewEpoch('new', 4002)
    Assert-True ($queue.Due('new', 4002, 1)[0].InitialRegistration) 'New session added the modification delay to a created file.'
    $null = $queue.Enqueue('existing', 'edited.pdf', 'upsert', $null, 5000)
    Assert-True ($queue.Due('existing', 124999, 1).Length -eq 0) 'Existing file skipped the 120-second quiet period.'
    $null = $queue.Enqueue('existing', 'edited.pdf', 'create', $null, 5001)
    Assert-True ($queue.Due('existing', 125000, 1).Length -eq 0) 'Late create event promoted an existing modification.'
    $null = $queue.Enqueue('unicode', ('Stra' + [char]0x00df + 'e.pdf'), 'upsert', $null, 0)
    $null = $queue.Enqueue('unicode', 'STRASSE.PDF', 'upsert', $null, 1)
    Assert-True ($queue.Count('unicode') -eq 1) 'Queue normalization differs from server Unicode casefold.'
    $entry = $queue.Due('unicode', 120001, 1)[0]
    Assert-True ($queue.Freeze('unicode', 1, 1, 'retry-1', '{}', [DocMind.SyncEntry[]]@($entry))) 'Retry fixture could not freeze.'
    foreach ($delay in @(5000,10000,20000,40000,80000,160000,300000,300000)) {
        $queue.Retry('unicode', 'retry-1', 1000000, $false)
        Assert-True ($queue.Outbox('unicode').NextAttemptAt -eq (1000000 + $delay)) 'Retry delay did not follow bounded exponential backoff.'
    }
    $queue.Retry('unicode', 'retry-1', 1000000, $true)
    $queue.Dispose()
    $queue = [DocMind.SyncQueue]::new($dbPath)
    Assert-True ($queue.Outbox('unicode').Held) 'Permanent error hold did not survive restart.'
    $fingerprint = 'a' * 64
    $entry = $queue.Due('other', 123001, 1)[0]
    Assert-True ($queue.RememberFingerprint($entry, $fingerprint)) 'First fingerprint was not persisted.'
    $queue.Dispose(); $queue = [DocMind.SyncQueue]::new($dbPath)
    Assert-True ($queue.Due('other', 123001, 1)[0].Fingerprint -eq $fingerprint) 'Fingerprint did not survive restart.'
    $queue.ResetForNewEpoch('unicode', 2000000)
    Assert-True ($null -eq $queue.Outbox('unicode')) 'Stale epoch outbox survived fencing.'
    Assert-True ($queue.Count('unicode') -eq 1) 'Epoch reset dropped unresolved observation.'
    $scanGeneration = $queue.EnqueueScan('scan', 'scanned.pdf', 3000000)
    $scanned = $queue.Due('scan', 3000000, 1)[0]
    Assert-True ($scanned.InitialRegistration -and $scanned.Generation -eq $scanGeneration) 'Scan discovery waited for modification debounce.'
    Assert-True ($queue.RememberFingerprint($scanned, $fingerprint)) 'Scan fingerprint could not be remembered.'
    Assert-True ($queue.Freeze('scan', 1, 1, 'scan-first', '{}', [DocMind.SyncEntry[]]@($scanned))) 'Scan first observation could not freeze.'
    Assert-True ($queue.EnqueueScan('scan', 'SCANNED.PDF', 3000001) -eq $scanGeneration) 'Repeated scan replaced an in-flight generation.'
    Assert-True ($queue.Outbox('scan').RequestId -eq 'scan-first') 'Scan replaced immutable outbox.'
    $queue.Acknowledge('scan', 'scan-first', [int[]]@(1), 3000002)
    Assert-True ($queue.EnqueueScan('scan', 'scanned.pdf', 3005000) -eq $scanGeneration) 'Repeated scan restarted observation.'
    Assert-True ($queue.Due('scan', 3010001, 1).Length -eq 0) 'Repeated scan bypassed ten-second stability interval.'
    $scanned = $queue.Due('scan', 3010002, 1)[0]
    Assert-True ($scanned.StableCount -eq 1 -and $scanned.Fingerprint -eq $fingerprint) 'Repeated scan lost stability evidence.'
    $queue.Dispose(); $queue = [DocMind.SyncQueue]::new($dbPath)
    $queue.ResetForNewEpoch('scan', 3010003)
    Assert-True ($queue.Due('scan', 3010003, 1)[0].InitialRegistration) 'Scan timing was lost after restart and epoch change.'
    foreach ($kind in @('upsert','delete','move')) {
        $eventSource = 'scan-' + $kind
        $oldPath = if ($kind -eq 'move') { 'original.pdf' } else { $null }
        $eventGeneration = $queue.Enqueue($eventSource, 'pending.pdf', $kind, $oldPath, 4000000)
        Assert-True ($queue.EnqueueScan($eventSource, 'pending.pdf', 4000001) -eq $eventGeneration) 'Scan replaced a captured event generation.'
        $pending = $queue.Dirty($eventSource, 1)[0]
        Assert-True ($pending.Kind -eq $kind -and $pending.OldRelativePath -eq $oldPath) 'Scan lost event kind or move origin.'
        Assert-True ($pending.DueAt -eq $(if ($kind -eq 'delete') { 4000000 } else { 4120000 })) 'Scan changed an existing event deadline.'
    }
    $null = $queue.Enqueue('scan', 'scanned.pdf', 'upsert', $null, 3010004)
    Assert-True ($queue.Due('scan', 3010004, 1)[0].Fingerprint -eq $null) 'Modification kept stale scan fingerprint.'
    $null = $queue.EnqueueScan('priority', 'second.pdf', 5000000)
    $second = $queue.Due('priority', 5000000, 1)[0]
    $queue.Delay($second, 5000000, 10000, 1)
    $null = $queue.EnqueueScan('priority', 'first.pdf', 5000001)
    Assert-True ($queue.Due('priority', 5010000, 1)[0].RelativePath -eq 'second.pdf') 'Due second observation was starved by first observations.'
    $heldGeneration=$queue.EnqueueScan('held-scan','unconfigured.pdf',6000000)
    $held=$queue.Due('held-scan',6000000,1)[0]
    Assert-True ($queue.Freeze('held-scan',1,1,'held-first','{}',[DocMind.SyncEntry[]]@($held))) 'Held recovery fixture failed.'
    $queue.Acknowledge('held-scan','held-first',[int[]]@(3),6000001)
    Assert-True ($queue.Due('held-scan',6000002,1).Length -eq 0) 'Held fixture was still selectable.'
    Assert-True ($queue.EnqueueScan('held-scan','unconfigured.pdf',6000002) -eq $heldGeneration) 'Recovery scan replaced held event generation.'
    Assert-True ($queue.Due('held-scan',6000002,1).Length -eq 1) 'Recovery scan could not retry an acknowledged held file.'
    Write-Output 'PASS: durable queue, autosaves, created-file timing, 120s/10s/2s timing, immutable outbox, generation-safe ACK and restart.'
} finally {
    if ($null -ne $queue) { $queue.Dispose() }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
