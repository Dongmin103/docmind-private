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
    $Once=[bool]$OnePass;$PreviewOnly=$false;$ClaimJobId=if($Targeted){'target-job'}else{''};$ClaimSourceId='';$SkipRetries=$false
    $allowedClaimFormats=@();$previewEnabled=[bool]$Preview;$consecutivePreviewClaims=0
    $lastReceiptReplayAt=[DateTimeOffset]::UtcNow.AddDays(1);$cleanupReceiptReplaySeconds=30
    $config=[pscustomobject]@{worker_id='worker-test'};$claimLeaseSeconds=300;$claimPath='/ingest';$previewClaimPath='/preview';$PollSeconds=5
    $lastFencingTokenByJob=@{};$script:jobs=0;$script:sleeps=0;$script:claims=[Collections.Generic.List[string]]::new()
    function Get-DocMindClaimRequestBody {param($Purpose,$IngestionBody) return $IngestionBody}
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
$result=Invoke-TestLoop -Mode claim-error
Assert-True ($result.Jobs -eq 0 -and $result.Sleeps -eq 1) 'Claim failure did not back off.'
$result=Invoke-TestLoop -Mode success -OnePass
Assert-True ($result.Jobs -eq 1 -and $result.Sleeps -eq 0) 'Once did not perform one pass.'
$result=Invoke-TestLoop -Mode success -Preview
Assert-True (($result.Claims -join ',') -eq '/preview,/preview,/preview,/ingest') 'Preview fairness lost the ingestion turn after three previews.'
$result=Invoke-TestLoop -Mode success -Preview -Targeted -OnePass
Assert-True (($result.Claims -join ',') -eq '/ingest') 'Targeted ingestion claim was replaced by a preview.'
'PASS: continuous claims, finalized failures, idle/error backoff, Once, preview fairness and targeted ingestion.'
