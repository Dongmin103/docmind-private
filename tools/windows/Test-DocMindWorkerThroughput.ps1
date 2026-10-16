[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Start-DocMindUEncryptorHostWorker.ps1'),[ref]$tokens,[ref]$errors)
Assert-True ($errors.Count -eq 0) 'Worker parse failed.'
$loops=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.DoWhileStatementAst]},$true))
$workerLoop=$loops[-1].Extent.Text
function Invoke-TestLoop {
    param([string]$Mode,[switch]$OnePass,[switch]$Preview,[switch]$Targeted)
    $Once=[bool]$OnePass;$Purpose=if($Preview -and -not $Targeted){'preview'}else{'ingest'};$ClaimJobId=if($Targeted){'target-job'}else{''};$ClaimSourceId='';$SkipRetries=$false
    $allowedClaimFormats=@();$previewEnabled=[bool]$Preview;
    $lastReceiptReplayAt=[DateTimeOffset]::UtcNow.AddDays(1);$cleanupReceiptReplaySeconds=30
    $config=[pscustomobject]@{worker_id='worker-test'};$claimLeaseSeconds=300;$claimPath='/ingest';$previewClaimPath='/preview';$PollSeconds=5
    $IngestionSlot=-1
    $lastFencingTokenByJob=@{};$script:jobs=0;$script:sleeps=0;$script:claims=[Collections.Generic.List[string]]::new()
    function Get-DocMindClaimRequestBody {param($Purpose,$IngestionBody) return $IngestionBody}
    function Invoke-DocMindRoleReaper { param($ControlRoot,$WorkRoot,$ReceiptRoot,$MinimumAgeSeconds) return 0 }
    function ConvertTo-PreviewLease {param($Claim) return $Claim}
    function Invoke-SignedJsonRequest {
        param($Method,$RelativeUri,$Body)
        $script:claims.Add($RelativeUri)
        if($Mode -eq 'claim-error'){throw 'API_HTTP_503'}
        $available=$script:jobs -lt 3 -or $Preview
        $job=if($available){[pscustomobject]@{job_id=('job-'+$script:jobs);fencing_token=1L}}else{$null}
        return [pscustomobject]@{Body=[Text.Encoding]::UTF8.GetBytes((@{job=$job}|ConvertTo-Json -Compress))}
    }
    function Invoke-LeasedDecryptJob {
        param($Lease,$Purpose,[ref]$Finalized)
        $script:jobs++
        if($null -ne $Finalized){$Finalized.Value=($Mode -ne 'unsafe-failure')}
        if($Preview -and $script:jobs -eq 4){Set-Variable -Name Once -Value $true -Scope 1}
        if($Mode -eq 'safe-failure' -and $script:jobs -eq 1){throw 'NONZERO_EXIT'}
        if($Mode -eq 'unsafe-failure'){throw 'STATUS_ACK_FAILED'}
        if($Mode -eq 'artifact-error'){throw 'API_HTTP_503'}
    }
    function Start-Sleep {param($Seconds) $script:sleeps++;Set-Variable -Name Once -Value $true -Scope 1}
    Invoke-Expression $workerLoop
    return [pscustomobject]@{Jobs=$script:jobs;Sleeps=$script:sleeps;Claims=@($script:claims)}
}
$result=Invoke-TestLoop -Mode success
Assert-True ($result.Jobs -eq 3 -and $result.Sleeps -eq 1) 'Worker slept between ready jobs instead of only on the empty queue.'
$result=Invoke-TestLoop -Mode safe-failure
Assert-True ($result.Jobs -eq 3 -and $result.Sleeps -eq 1) 'Finalized document failure delayed healthy jobs.'
$result=Invoke-TestLoop -Mode unsafe-failure
Assert-True ($result.Jobs -eq 1 -and $result.Sleeps -eq 1) 'Unconfirmed cleanup/status did not back off.'
$result=Invoke-TestLoop -Mode artifact-error
Assert-True ($result.Jobs -eq 1 -and $result.Sleeps -eq 1) 'Finalized artifact transport failure did not back off.'
$result=Invoke-TestLoop -Mode claim-error
Assert-True ($result.Jobs -eq 0 -and $result.Sleeps -eq 1) 'Claim failure did not back off.'
$result=Invoke-TestLoop -Mode success -OnePass
Assert-True ($result.Jobs -eq 1 -and $result.Sleeps -eq 0) 'Once did not perform one pass.'
$result=Invoke-TestLoop -Mode success -Preview
Assert-True (($result.Claims -join ',') -eq '/preview,/preview,/preview,/preview') 'Preview worker claimed ingestion.'
$result=Invoke-TestLoop -Mode success -Preview -Targeted -OnePass
Assert-True (($result.Claims -join ',') -eq '/ingest') 'Targeted ingestion claim was replaced by a preview.'

# Exercise the actual decrypt-job cleanup/status path with a synthetic local
# executable. Only external transport is replaced; no real uEncryptor or API.
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
$testMutexName='Local\DocMind-ThroughputTest-'+[Guid]::NewGuid().ToString('n')
Invoke-Expression ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')).Replace('Global\DocMind-uEncryptor2-SingleRun', $testMutexName))
$base=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/worker-throughput-tests'))
$testRoot=Join-Path $base ([Guid]::NewGuid().ToString('n'))
[IO.Directory]::CreateDirectory($testRoot)|Out-Null
try {
    $executable=Join-Path $testRoot 'synthetic.exe'
    Add-Type -TypeDefinition 'using System.IO; public class SyntheticDecrypt { public static int Main(string[] args) { File.Copy(args[0], args[1]); return 0; } }' -OutputAssembly $executable -OutputType ConsoleApplication
    foreach($name in @('Get-HostTimingNanoseconds','Invoke-LeasedDecryptJob')){
        $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false)
        Assert-True ($null -ne $node) ('Missing worker function: '+$name)
        $definition=$node.Extent.Text.Replace('Global\DocMind-uEncryptor2-SingleRun',('Local\DocMind-Test-'+[Guid]::NewGuid().ToString('n')))
        Invoke-Expression $definition
    }
    $controlRoot=Join-Path $testRoot 'control'
    $sourceRoot=Join-Path $testRoot 'source';[IO.Directory]::CreateDirectory($sourceRoot)|Out-Null
    $sourceFile=Join-Path $sourceRoot 'sample.pdf';[IO.File]::WriteAllText($sourceFile,'synthetic only')
    $sources=@{'synthetic-source'=$sourceRoot};$workRoot=Join-Path $testRoot 'work';$receiptRoot=Join-Path $testRoot 'receipts'
    $jobsPath='/jobs';$previewJobsPath='/previews';$leaseSafetySeconds=5;$timeoutSeconds=5;$MaxPdfPages=0;$config=[pscustomobject]@{worker_id='synthetic-worker'}
    $lease=[pscustomobject]@{job_id='synthetic-job';source_id='synthetic-source';document_id='synthetic-document';version_id='synthetic-version';relative_path='sample.pdf';ciphertext_sha256=(Get-DocMindFileSha256 -LiteralPath $sourceFile);fencing_token=1L;lease_expires_at=[DateTimeOffset]::UtcNow.AddMinutes(5).ToString('o')}
    function Assert-ExecutableIdentity {return ('a'*64)}
    function Save-HostTiming {}
    $script:leaseRevoked=$false;$script:leaseChecks=0;$script:validationTimeoutMin=10;$script:validationTimeoutMax=10
    function Invoke-SignedJsonRequest {
        param($Method,$RelativeUri,$Body,$RequestTimeoutSeconds)
        Assert-True ($Method -eq 'POST' -and $RelativeUri -eq '/jobs/synthetic-job/validate') 'Ingestion validation used an unexpected endpoint.'
        Assert-True ($Body.worker_id -eq $config.worker_id -and $Body.version_id -eq $lease.version_id -and $Body.fencing_token -eq $lease.fencing_token) 'Ingestion validation omitted lease identity.'
        Assert-True ($RequestTimeoutSeconds -ge $script:validationTimeoutMin -and $RequestTimeoutSeconds -le $script:validationTimeoutMax) 'Ingestion validation request did not use its bounded budget.'
        $script:leaseChecks++
        if($script:leaseRevoked){throw 'API_HTTP_409'}
        return [pscustomobject]@{Body=[Text.Encoding]::UTF8.GetBytes((@{valid=$true;lease_expires_at=$lease.lease_expires_at}|ConvertTo-Json -Compress))}
    }
    function Invoke-SignedBytesRequest {throw $script:artifactError}
    function Send-WorkerStatus {
        param($Lease,$State,$ErrorCode,$CleanupComplete,$Purpose)
        if($script:statusFails){throw 'API_HTTP_503'}
        Assert-True ($State -eq 'FAILED' -and $CleanupComplete) 'Artifact failure was not reported with completed cleanup.'
        $script:statusCalls++
    }
    foreach($code in @('API_HTTP_503','API_RESPONSE_AUTHENTICATION_FAILED')){
        $script:artifactError=$code;$script:statusFails=$false;$script:statusCalls=0;$finalized=$false;$caught=''
        try{Invoke-LeasedDecryptJob -Lease $lease -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
        Assert-True ($caught -eq $code -and $finalized -and $script:statusCalls -eq 1) 'Actual artifact failure did not retain its error and confirmed cleanup/status signal.'
        Assert-True (@(Get-ChildItem -LiteralPath $workRoot -Directory).Count -eq 0) 'Failed artifact left temporary plaintext.'
    }
    # Hold the real isolated gate beyond the old five-second ingestion cap.
    # The actual worker must then reach artifact delivery and complete cleanup.
    $holderScript=Join-Path $testRoot 'hold-gate.ps1'
    $holderReady=Join-Path $testRoot 'holder.ready'
    [IO.File]::WriteAllText($holderScript, @'
param($Name,$Ready)
$m=[Threading.Mutex]::new($false,$Name)
try {
    [void]$m.WaitOne()
    [IO.File]::WriteAllText($Ready,'ready')
    Start-Sleep -Seconds 7
} finally { $m.ReleaseMutex();$m.Dispose() }
'@)
    $holder=Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$holderScript+'"'),('-Name "'+$testMutexName+'"'),('-Ready "'+$holderReady+'"')) -WindowStyle Hidden -PassThru
    try {
        $readyDeadline=[DateTimeOffset]::UtcNow.AddSeconds(15)
        while(-not (Test-Path -LiteralPath $holderReady)){
            if($holder.HasExited -or [DateTimeOffset]::UtcNow -gt $readyDeadline){throw 'Synthetic mutex holder did not start.'}
            Start-Sleep -Milliseconds 20
        }
        $script:artifactError='API_HTTP_503';$script:statusFails=$false;$script:statusCalls=0;$finalized=$false;$caught=''
        $waitClock=[Diagnostics.Stopwatch]::StartNew()
        try{Invoke-LeasedDecryptJob -Lease $lease -Purpose ingest -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
        Assert-True ($caught -eq 'API_HTTP_503' -and $finalized -and $script:statusCalls -eq 1) ('Queued ingestion failed before artifact delivery: '+$caught)
        Assert-True ($waitClock.Elapsed.TotalSeconds -gt 5) 'Regression did not exercise a wait over five seconds.'
    } finally { $holder.WaitForExit();$holder.Dispose() }

    # Waiting must not outlive the lease safety budget or launch a decryptor.
    $originalGateFunction=${function:Enter-DocMindDecryptGate}
    $originalDecryptFunction=${function:Invoke-DocMindDecryptProcess}
    $originalLeaseExpiry=$lease.lease_expires_at
    $script:decryptStarts=0
    function Invoke-DocMindDecryptProcess { $script:decryptStarts++;throw 'UNEXPECTED_DECRYPT_START' }
    function Enter-DocMindDecryptGate {
        param($ControlRoot,$DeadlineUtc,$ValidateLease)
        $expected=(ConvertTo-DocMindDateTimeOffset -Value $lease.lease_expires_at -Name 'expiry').AddSeconds(-10)
        Assert-True ([Math]::Abs(($DeadlineUtc-$expected).TotalMilliseconds) -lt 100) 'Ingestion gate did not use the lease safety budget.'
        Start-Sleep -Milliseconds 2200
        & $ValidateLease
        throw 'VALIDATION_DID_NOT_REJECT_EXPIRED_LEASE'
    }
    $lease.lease_expires_at=[DateTimeOffset]::UtcNow.AddSeconds(12).ToString('o')
    $caught='';$finalized=$false;$script:statusCalls=0
    try{Invoke-LeasedDecryptJob -Lease $lease -Purpose ingest -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -eq 'LEASE_EXPIRED_BEFORE_DECRYPT' -and $script:decryptStarts -eq 0 -and $finalized) 'Expired queued ingestion started decryption or lost cleanup.'
    $lease.lease_expires_at=$originalLeaseExpiry
    function Enter-DocMindDecryptGate {
        param($ControlRoot,$DeadlineUtc,$ValidateLease)
        & $ValidateLease
        throw 'BOUNDARY_VALIDATED'
    }
    $script:validationTimeoutMin=1;$script:validationTimeoutMax=2
    $lease.lease_expires_at=[DateTimeOffset]::UtcNow.AddSeconds(13).ToString('o')
    $caught='';$finalized=$false;$script:leaseChecks=0
    try{Invoke-LeasedDecryptJob -Lease $lease -Purpose ingest -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -eq 'BOUNDARY_VALIDATED' -and $script:leaseChecks -eq 1 -and $script:decryptStarts -eq 0 -and $finalized) 'Validation exceeded the remaining lease safety budget.'
    $lease.lease_expires_at=$originalLeaseExpiry
    $script:validationTimeoutMin=10;$script:validationTimeoutMax=10
    # A server-side revocation during a long wait must prevent decrypt launch
    # even though the captured expiry and source bytes are still valid.
    function Enter-DocMindDecryptGate {
        param($ControlRoot,$DeadlineUtc,$ValidateLease)
        & $ValidateLease
        $script:leaseRevoked=$true
        & $ValidateLease
        throw 'VALIDATION_DID_NOT_REJECT_REVOKED_LEASE'
    }
    $caught='';$finalized=$false;$script:leaseChecks=0
    try{Invoke-LeasedDecryptJob -Lease $lease -Purpose ingest -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -eq 'API_HTTP_409' -and $script:leaseChecks -eq 2 -and $script:decryptStarts -eq 0 -and $finalized) 'Revoked queued ingestion started decryption or lost cleanup.'
    $script:leaseRevoked=$false
    # A lease revoked after gate admission is checked again just before launch.
    function Enter-DocMindDecryptGate {
        param($ControlRoot,$DeadlineUtc,$ValidateLease)
        $acquired=& $originalGateFunction -ControlRoot $ControlRoot -DeadlineUtc $DeadlineUtc -ValidateLease $ValidateLease
        $script:leaseRevoked=$true
        return $acquired
    }
    $caught='';$finalized=$false
    try{Invoke-LeasedDecryptJob -Lease $lease -Purpose ingest -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -eq 'API_HTTP_409' -and $script:decryptStarts -eq 0 -and $finalized) 'Revocation after gate acquisition started decryption.'
    $script:leaseRevoked=$false
    # Preview waiting remains capped at thirty seconds, independently of the
    # longer ingestion lease. Exercise the actual worker's selected deadline.
    function Enter-DocMindDecryptGate {
        param($ControlRoot,$DeadlineUtc,$ValidateLease)
        $seconds=($DeadlineUtc-[DateTimeOffset]::UtcNow).TotalSeconds
        Assert-True ($seconds -gt 28 -and $seconds -le 30) 'Preview wait cap changed.'
        throw 'PREVIEW_DECRYPT_BUSY'
    }
    $caught='';$finalized=$false
    try{Invoke-LeasedDecryptJob -Lease $lease -Purpose preview -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -eq 'PREVIEW_DECRYPT_BUSY' -and $script:decryptStarts -eq 0 -and $finalized) 'Preview deadline regression or missing cleanup.'
    ${function:Enter-DocMindDecryptGate}=$originalGateFunction
    ${function:Invoke-DocMindDecryptProcess}=$originalDecryptFunction

    $script:statusFails=$true;$finalized=$false
    try{Invoke-LeasedDecryptJob -Lease $lease -Finalized ([ref]$finalized)}catch{}
    Assert-True (-not $finalized) 'Failed status ACK marked actual job finalized.'
    $script:statusFails=$false
    function Invoke-DocMindDecryptProcess { param($Gate,$Executable,$SourceFile,$OutputFile,$TimeoutSeconds) $Gate.ProcessExitConfirmed=$false; throw 'PROCESS_TERMINATION_FAILED' }
    function Send-WorkerStatus { param($Lease,$State,$ErrorCode,$CleanupComplete,$Purpose) Assert-True ($State -ne 'COMPLETE' -and $ErrorCode -eq 'PROCESS_TERMINATION_FAILED' -and -not $CleanupComplete) 'Termination failure was overwritten by completion or cleanup.' }
    $caught='';try{Invoke-LeasedDecryptJob -Lease $lease -Finalized ([ref]$finalized)}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -eq 'PROCESS_TERMINATION_FAILED' -and -not $finalized) 'Unsafe termination was reported finalized.'

} finally {
    $resolved=[IO.Path]::GetFullPath($testRoot)
    if(-not $resolved.StartsWith($base+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe worker-test cleanup.'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
'PASS: continuous claims, finalized failures, idle/error backoff, Once, isolated preview claims and targeted ingestion.'
