[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Prepare', 'Apply', 'Rollback')][string]$Action,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$LegacyTaskName = 'DocMind Host Worker',
    [string]$LegacyConfigPath = '',
    [string]$MemoryReportPath = '',
    [string]$FunctionalReportPath = '',
    [switch]$BackupExclusionAcknowledged
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
$script:DocMindIsolationToolRoot = $PSScriptRoot

function Save-DocMindIsolationManifest {
    param($Manifest, [string]$Path)
    Write-DocMindGateRecord -Path $Path -Record $Manifest
}

function Get-DocMindTaskState {
    param([string]$Name)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) { return 'Missing' }
    return [string]$task.State
}

function Set-DocMindIsolationTask {
    param([string]$Name, [string]$Operation, [string]$Xml)
    switch ($Operation) {
        register { Register-ScheduledTask -TaskName $Name -Xml $Xml -Force | Out-Null }
        disable { if ((Get-DocMindTaskState $Name) -ne 'Missing') { Disable-ScheduledTask -TaskName $Name | Out-Null } }
        enable { Enable-ScheduledTask -TaskName $Name | Out-Null }
        stop {
            if ((Get-DocMindTaskState $Name) -ne 'Missing') {
                Stop-ScheduledTask -TaskName $Name
                $until = [DateTimeOffset]::UtcNow.AddSeconds(10)
                while ((Get-DocMindTaskState $Name) -eq 'Running') {
                    if ([DateTimeOffset]::UtcNow -ge $until) { throw 'WORKER_STOP_UNCONFIRMED' }
                    Start-Sleep -Milliseconds 100
                }
            }
        }
        start { Start-ScheduledTask -TaskName $Name }
        remove { if ((Get-DocMindTaskState $Name) -ne 'Missing') { Unregister-ScheduledTask -TaskName $Name -Confirm:$false } }
    }
}

function Assert-DocMindIsolationArtifacts {
    param($Manifest)
    foreach ($artifact in @($Manifest.artifacts)) {
        if (-not (Test-Path -LiteralPath $artifact.path -PathType Leaf) -or (Get-DocMindFileSha256 $artifact.path) -ne $artifact.sha256) { throw 'PREPARED_ARTIFACT_CHANGED' }
    }
    foreach ($artifact in @($Manifest.original_files)) {
        if ((Get-DocMindFileSha256 $artifact.path) -ne $artifact.sha256 -or (Get-DocMindFileSha256 $artifact.snapshot) -ne $artifact.sha256) { throw 'ORIGINAL_ARTIFACT_CHANGED' }
    }
    foreach ($worker in @($Manifest.workers)) {
        $config = Get-Content -LiteralPath $worker.config -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $config.backup_exclusion_acknowledged) { throw 'NEW_ROOT_BACKUP_EXCLUSION_REQUIRED' }
        if ($config.require_bitlocker) {
            $volume = Get-BitLockerVolume -MountPoint (Split-Path -Qualifier ([string]$config.work_root)) -ErrorAction Stop
            if ([string]$volume.ProtectionStatus -ne 'On') { throw 'BITLOCKER_PROTECTION_REQUIRED' }
        }
        $paths = Get-DocMindWorkerRolePaths -Config $config -Purpose $worker.purpose
        foreach ($root in @($paths.WorkRoot, $paths.ReceiptRoot, $paths.ControlRoot)) {
            Assert-DocMindNoReparsePoint -LiteralPath $root -Boundary ([IO.Path]::GetPathRoot($root)) -Name 'Deployment root'
            foreach ($source in $config.sources) {
                $a = $root.TrimEnd('\'); $b = [IO.Path]::GetFullPath($source.root).TrimEnd('\')
                if ($a.Equals($b, [StringComparison]::OrdinalIgnoreCase) -or $a.StartsWith($b + '\', [StringComparison]::OrdinalIgnoreCase) -or $b.StartsWith($a + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'SOURCE_ROOT_OVERLAP' }
            }
        }
    }
}

function Assert-DocMindIsolationQuiescent {
    param($Manifest, [switch]$AllowLegacyReceipts)
    Assert-DocMindDecryptControlSafe -ControlRoot $Manifest.control_root
    foreach ($root in @($Manifest.work_roots)) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        Assert-DocMindNoReparsePoint -LiteralPath $root -Boundary ([IO.Path]::GetPathRoot($root)) -Name 'Plaintext root'
        # Nested previews directory itself is allowed; any job/file is not.
        foreach ($entry in Get-ChildItem -LiteralPath $root -Force) {
            if ($entry.PSIsContainer -and $entry.Name -eq 'previews' -and @(Get-ChildItem -LiteralPath $entry.FullName -Force).Count -eq 0) { continue }
            throw 'UNRESOLVED_PLAINTEXT_WORK'
        }
    }
    foreach ($root in @($Manifest.receipt_roots)) {
        if ($AllowLegacyReceipts -and $root -in @($Manifest.legacy_receipt_roots)) { continue }
        if ((Test-Path -LiteralPath $root) -and @(Get-ChildItem -LiteralPath $root -File -Force).Count -gt 0) { throw 'UNRESOLVED_CLEANUP_RECEIPTS' }
    }
}

function Enter-DocMindIsolationTransition {
    param($Manifest)
    $handles = [Collections.Generic.List[object]]::new()
    try {
        $gate = Enter-DocMindDecryptGate -ControlRoot $Manifest.control_root -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(1)) -ValidateLease { }
        $handles.Add($gate)
        return ,$handles.ToArray()
    } catch { Exit-DocMindIsolationTransition -Handles $handles.ToArray(); throw }
}

function Exit-DocMindIsolationTransition {
    param($Handles)
    foreach ($gate in @($Handles)) { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $true }
}

function Invoke-DocMindLegacyReceiptRecovery {
    param($Manifest)
    # Reuse the verified signed transport, but retain the original worker ID,
    # original roots, version and fence until every original receipt is ACKed.
    $config = Get-Content -LiteralPath $Manifest.legacy_config -Raw -Encoding UTF8 | ConvertFrom-Json
    $apiBase = [Uri]::new([string]$config.api_base_uri)
    $secret = Read-DocMindSharedSecret -LiteralPath $config.shared_secret_file
    $responseNonces = @{}
    $jobsPath = '/api/v1/cloud-sync/host-worker/jobs'; $previewJobsPath = '/api/v1/cloud-sync/host-worker/previews'
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Manifest.signed_transport, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'TRANSPORT_PARSE_FAILED' }
    foreach ($name in @('New-SignedRequestHeaders', 'Get-RequiredResponseHeader', 'Assert-SignedResponse', 'Invoke-SignedBytesRequest', 'Invoke-SignedJsonRequest', 'Send-WorkerStatusBody', 'Send-CleanupReceipt')) {
        $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $false)
        Invoke-Expression $node.Extent.Text
    }
    foreach ($role in @('ingest', 'preview')) {
        $root = $Manifest.legacy_receipt_roots[$([int]($role -eq 'preview'))]
        $work = $Manifest.legacy_work_roots[$([int]($role -eq 'preview'))]
        $result = Invoke-DocMindCleanupReceiptReplay -ReceiptRoot $root -WorkRoot $work -Sender { param($receipt) Send-CleanupReceipt -Receipt $receipt -Purpose $role }
        if ($result.pending) { throw 'LEGACY_RECEIPT_ACK_REQUIRED' }
    }
}

function Wait-DocMindIsolationApi {
    param($Manifest)
    $base = [string]$Manifest.api_base_uri
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        try {
            $health = Invoke-WebRequest -Uri ($base.TrimEnd('/') + '/api/v1/system/healthz') -UseBasicParsing -TimeoutSec 2
            if ($health.StatusCode -ne 200) { throw 'API_NOT_READY' }
            try {
                Invoke-WebRequest -Uri ($base.TrimEnd('/') + '/api/v1/cloud-sync/host-worker/previews/readiness/validate') -Method POST -Body '{}' -ContentType 'application/json' -UseBasicParsing -TimeoutSec 2 | Out-Null
            } catch {
                if ($null -ne $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) { return }
            }
        } catch { }
        Start-Sleep -Seconds ([Math]::Min(5, [Math]::Pow(2, $attempt)))
    }
    throw 'PREVIEW_API_NOT_READY'
}

function Restore-DocMindOriginalTask {
    param($Manifest)
    foreach ($worker in @($Manifest.workers)) {
        Set-DocMindIsolationTask -Name $worker.name -Operation disable
        Set-DocMindIsolationTask -Name $worker.name -Operation stop
    }
    Assert-DocMindIsolationQuiescent -Manifest $Manifest
    Assert-DocMindNoRoleWorkers -Manifest $Manifest
    Set-DocMindIsolationTask -Name $Manifest.original.name -Operation register -Xml $Manifest.original.xml
    $operation = if ($Manifest.original.enabled) { 'enable' } else { 'disable' }
    Set-DocMindIsolationTask -Name $Manifest.original.name -Operation $operation
    if ($Manifest.original.running) { Set-DocMindIsolationTask -Name $Manifest.original.name -Operation start }
}

function Assert-DocMindNoRoleWorkers {
    param($Manifest)
    foreach ($role in @('ingest', 'preview')) {
        $singleton = Enter-DocMindWorkerSingleton -Purpose $role
        $singleton.ReleaseMutex(); $singleton.Dispose()
    }
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'")) {
        if ($process.ProcessId -eq $PID) { continue }
        foreach ($path in @($Manifest.legacy_launcher, $Manifest.legacy_worker)) {
            if ($process.CommandLine -and $process.CommandLine.IndexOf($path, [StringComparison]::OrdinalIgnoreCase) -ge 0) { throw 'LEGACY_WORKER_STOP_UNCONFIRMED' }
        }
    }
}

function Initialize-DocMindRoleRoots {
    param($Worker)
    foreach ($root in @($Worker.work_root, $Worker.receipt_root)) {
        [IO.Directory]::CreateDirectory($root) | Out-Null
        Protect-DocMindJobDirectory -LiteralPath $root
    }
}

function Invoke-DocMindIsolationTransition {
    param([string]$Action, $Manifest, [string]$ManifestPath)
    Assert-DocMindIsolationArtifacts -Manifest $Manifest
    if ($Action -eq 'Apply' -and $Manifest.phase -eq 'applied') { return }
    if ($Action -eq 'Rollback' -and $Manifest.phase -eq 'rolled-back') { return }
    if ($Action -eq 'Apply' -and (-not $Manifest.memory_gate.passed -or -not $Manifest.functional_gate.passed)) { throw 'ISOLATION_DEPLOYMENT_GATE_FAILED' }
    Assert-DocMindIsolationQuiescent -Manifest $Manifest -AllowLegacyReceipts:($Action -eq 'Apply')
    $handles = Enter-DocMindIsolationTransition -Manifest $Manifest
    try {
        Assert-DocMindIsolationQuiescent -Manifest $Manifest -AllowLegacyReceipts:($Action -eq 'Apply')
        if ($Action -eq 'Rollback') {
            Restore-DocMindOriginalTask -Manifest $Manifest
            $Manifest.phase = 'rolled-back'
            Save-DocMindIsolationManifest -Manifest $Manifest -Path $ManifestPath
            return
        }
        Invoke-DocMindLegacyReceiptRecovery -Manifest $Manifest
        Assert-DocMindIsolationQuiescent -Manifest $Manifest
        Wait-DocMindIsolationApi -Manifest $Manifest
        try {
            Set-DocMindIsolationTask -Name $Manifest.original.name -Operation disable
            Set-DocMindIsolationTask -Name $Manifest.original.name -Operation stop
            Assert-DocMindIsolationQuiescent -Manifest $Manifest
            Assert-DocMindNoRoleWorkers -Manifest $Manifest
            foreach ($worker in @($Manifest.workers)) {
                Initialize-DocMindRoleRoots -Worker $worker
                Set-DocMindIsolationTask -Name $worker.name -Operation register -Xml $worker.xml
                Set-DocMindIsolationTask -Name $worker.name -Operation disable
            }
            # Both registrations finish before either role starts.
            if ($Manifest.original.enabled) {
                foreach ($worker in @($Manifest.workers)) { Set-DocMindIsolationTask -Name $worker.name -Operation enable }
                if ($Manifest.original.running) { foreach ($worker in @($Manifest.workers)) { Set-DocMindIsolationTask -Name $worker.name -Operation start } }
            }
            $Manifest.phase = 'applied'
            Save-DocMindIsolationManifest -Manifest $Manifest -Path $ManifestPath
        } catch {
            $transitionFailure = $_
            try { Restore-DocMindOriginalTask -Manifest $Manifest }
            catch {
                $Manifest.phase = 'recovery-required'
                Save-DocMindIsolationManifest -Manifest $Manifest -Path $ManifestPath
                throw 'ISOLATION_RECOVERY_REQUIRED'
            }
            $Manifest.phase = 'rolled-back'
            Save-DocMindIsolationManifest -Manifest $Manifest -Path $ManifestPath
            throw $transitionFailure
        }
    } finally { Exit-DocMindIsolationTransition -Handles $handles }
}

function New-DocMindIsolationManifest {
    param([string]$Path, [string]$TaskName, [string]$ConfigPath, [string]$MemoryReport, [string]$FunctionalReport, [bool]$BackupExcluded)
    if (Test-Path -LiteralPath $Path) { throw 'MANIFEST_ALREADY_EXISTS' }
    $task = Get-ScheduledTask -TaskName $TaskName
    if (@($task.Actions).Count -ne 1 -or $task.Actions.Arguments -notmatch '-File\s+"([^"]+)"') { throw 'LEGACY_LAUNCHER_UNRESOLVED' }
    $launcher = [IO.Path]::GetFullPath($Matches[1])
    $launcherText = Get-Content -LiteralPath $launcher -Raw -Encoding UTF8
    $release = Split-Path $launcher -Parent
    if (-not $ConfigPath) { $ConfigPath = Join-Path $release 'host-worker.json' }
    $ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $config.preview_enabled) { throw 'PREVIEW_NOT_ENABLED' }
    $oldPaths = Get-DocMindWorkerRolePaths -Config $config -Purpose ingest
    $legacyWorker = Join-Path $release 'windows/Start-DocMindUEncryptorHostWorker.ps1'
    if (-not (Test-Path -LiteralPath $legacyWorker)) { throw 'LEGACY_WORKER_UNRESOLVED' }
    # Reject unfamiliar launchers instead of guessing their source/filter scope.
    if ($launcherText -notmatch "windows[\\/]Start-DocMindUEncryptorHostWorker.ps1" -or $launcherText -notmatch "host-worker.json") { throw 'LEGACY_LAUNCHER_UNSUPPORTED' }
    $filters = @()
    foreach ($match in [regex]::Matches($launcherText, '-(ClaimSourceId|ClaimFormats|PollSeconds)\s+(''[^'']*''|"[^"]*"|[A-Za-z0-9_,.-]+)')) { $filters += $match.Value }
    if ($launcherText -match '-SkipRetries\b') { $filters += '-SkipRetries' }
    if ($launcherText -match '-(Once|CleanupJobId|ClaimJobId|MaxPdfPages)\b') { throw 'LEGACY_DAEMON_ARGUMENTS_UNSUPPORTED' }
    $directory = Split-Path $Path -Parent
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    Protect-DocMindJobDirectory -LiteralPath $directory
    $before = Join-Path $directory 'before'; $after = Join-Path $directory 'after'
    [IO.Directory]::CreateDirectory($before) | Out-Null; [IO.Directory]::CreateDirectory($after) | Out-Null
    $originalFiles = @(); $artifacts = @()
    foreach ($file in @($launcher, $ConfigPath, $legacyWorker, (Join-Path $release 'windows/DocMindUEncryptorHostWorker.Common.ps1'))) {
        $snapshot = Join-Path $before (([Guid]::NewGuid().ToString('n')) + '-' + [IO.Path]::GetFileName($file))
        Copy-Item -LiteralPath $file -Destination $snapshot
        $originalFiles += @{ path = $file; snapshot = $snapshot; sha256 = Get-DocMindFileSha256 $file }
    }
    foreach ($name in @('Start-DocMindUEncryptorHostWorker.ps1', 'DocMindUEncryptorHostWorker.Common.ps1', 'DocMindDecryptGate.ps1', 'Set-DocMindPreviewWorkerIsolation.ps1')) {
        $target = Join-Path $after $name
        Copy-Item -LiteralPath (Join-Path $script:DocMindIsolationToolRoot $name) -Destination $target
        $artifacts += @{ path = $target; sha256 = Get-DocMindFileSha256 $target }
    }
    $originalXml = Export-ScheduledTask -TaskName $TaskName
    [IO.File]::WriteAllText((Join-Path $before 'task.xml'), $originalXml)
    $workers = @()
    foreach ($role in @('ingest', 'preview')) {
        $name = 'DocMind Host Worker (' + $role + ')'
        if ((Get-DocMindTaskState $name) -ne 'Missing') { throw 'ISOLATION_TASK_NAME_CONFLICT' }
        $newConfig = $config | ConvertTo-Json -Depth 12 | ConvertFrom-Json
        $newConfig.worker_id = [string]$config.worker_id + '-' + $role
        Assert-DocMindIdentifier -Value $newConfig.worker_id -Name 'worker_id'
        $newConfig.work_root = $oldPaths.WorkRoot + '-' + $role
        $newConfig | Add-Member -NotePropertyName cleanup_receipt_root -NotePropertyValue ($oldPaths.ReceiptRoot + '-' + $role) -Force
        $newConfig | Add-Member -NotePropertyName decrypt_control_root -NotePropertyValue $oldPaths.ControlRoot -Force
        $newConfig.backup_exclusion_acknowledged = $BackupExcluded
        $configFile = Join-Path $after ($role + '.json')
        [IO.File]::WriteAllText($configFile, ($newConfig | ConvertTo-Json -Depth 12))
        $launchFile = Join-Path $after ('run-' + $role + '.ps1')
        $roleFilters = if ($role -eq 'ingest') { $filters -join ' ' } else { '' }
        $boot = @'
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Set-DocMindPreviewWorkerIsolation.ps1'), [ref]$tokens, [ref]$errors)
$node = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Wait-DocMindIsolationApi'}, $false)
Invoke-Expression $node.Extent.Text
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot '__ROLE__.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Wait-DocMindIsolationApi -Manifest $config
& (Join-Path $PSScriptRoot 'Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $PSScriptRoot '__ROLE__.json') -Purpose __ROLE__ __FILTERS__ -TimingRoot (Join-Path $PSScriptRoot 'timing-__ROLE__') *>&1 | Out-File -LiteralPath (Join-Path $PSScriptRoot '__ROLE__.log') -Append -Encoding UTF8
'@
        [IO.File]::WriteAllText($launchFile, $boot.Replace('__ROLE__', $role).Replace('__FILTERS__', $roleFilters))
        [xml]$xml = $originalXml
        $xml.Task.Actions.Exec.Command = [string]$task.Actions.Execute
        $xml.Task.Actions.Exec.Arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "' + $launchFile + '"'
        $xml.Task.Settings.MultipleInstancesPolicy = 'IgnoreNew'
        $enabledNode = $xml.Task.Settings.SelectSingleNode('*[local-name()="Enabled"]')
        if ($null -eq $enabledNode) {
            $enabledNode = $xml.CreateElement('Enabled', $xml.Task.NamespaceURI)
            [void]$xml.Task.Settings.AppendChild($enabledNode)
        }
        $enabledNode.InnerText = 'false'
        foreach ($file in @($configFile, $launchFile)) { $artifacts += @{ path = $file; sha256 = Get-DocMindFileSha256 $file } }
        $workers += @{ name = $name; purpose = $role; xml = $xml.OuterXml; config = $configFile; work_root = $newConfig.work_root; receipt_root = $newConfig.cleanup_receipt_root }
    }
    $gateReports = @()
    foreach ($report in @($MemoryReport, $FunctionalReport)) {
        if ($report) {
            $report = [IO.Path]::GetFullPath($report)
            $gateReports += (Get-Content -LiteralPath $report -Raw | ConvertFrom-Json)
            $artifacts += @{ path = $report; sha256 = Get-DocMindFileSha256 $report }
        } else { $gateReports += [pscustomobject]@{ passed = $false; reason = 'NOT_MEASURED' } }
    }
    $manifest = [pscustomobject]@{ phase = 'prepared'; prepared_utc = [DateTimeOffset]::UtcNow.ToString('o');
        original = @{ name = $TaskName; xml = $originalXml; enabled = [bool]$task.Settings.Enabled; running = ([string]$task.State -eq 'Running') };
        legacy_config = $ConfigPath; api_base_uri = $config.api_base_uri; control_root = $oldPaths.ControlRoot;
        legacy_launcher = $launcher; legacy_worker = $legacyWorker;
        legacy_work_roots = @($oldPaths.WorkRoot, (Join-Path $oldPaths.WorkRoot 'previews'));
        legacy_receipt_roots = @($oldPaths.ReceiptRoot, (Join-Path $oldPaths.ReceiptRoot 'previews'));
        work_roots = @($oldPaths.WorkRoot) + @($workers.work_root);
        receipt_roots = @($oldPaths.ReceiptRoot, (Join-Path $oldPaths.ReceiptRoot 'previews')) + @($workers.receipt_root);
        workers = $workers; artifacts = $artifacts; original_files = $originalFiles;
        signed_transport = (Join-Path $after 'Start-DocMindUEncryptorHostWorker.ps1');
        memory_gate = $gateReports[0]; functional_gate = $gateReports[1];
        containers = @(& docker inspect --format '{{.Id}} {{.Name}} {{.Image}} {{.State.StartedAt}}' @(& docker ps -q));
        watcher_states = @(Get-ScheduledTask | Where-Object TaskName -Match '^DocMind.*(Watcher|reconciliation)' | ForEach-Object { @{ name = $_.TaskName; enabled = [bool]$_.Settings.Enabled; state = [string]$_.State } }) }
    Save-DocMindIsolationManifest -Manifest $manifest -Path $Path
    return $manifest
}

$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$manifestFile = [IO.Path]::GetFullPath($ManifestPath)
if (-not $manifestFile.StartsWith((Join-Path $repo '.local') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'MANIFEST_REQUIRES_IGNORED_LOCAL_PATH' }
if ($Action -eq 'Prepare') {
    New-DocMindIsolationManifest -Path $manifestFile -TaskName $LegacyTaskName -ConfigPath $LegacyConfigPath -MemoryReport $MemoryReportPath -FunctionalReport $FunctionalReportPath -BackupExcluded ([bool]$BackupExclusionAcknowledged) | Out-Null
    'Prepared exact deployment artifacts; live tasks unchanged.'
} else {
    $manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Invoke-DocMindIsolationTransition -Action $Action -Manifest $manifest -ManifestPath $manifestFile
    'Worker transition finished; see manifest phase.'
}
