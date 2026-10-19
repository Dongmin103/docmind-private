[CmdletBinding()]
param([string]$DeploymentPath,[switch]$StartNow)
$ErrorActionPreference='Stop'
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if(-not $DeploymentPath){$DeploymentPath=Join-Path $repoRoot '.local/cpu-server'}
$deployment=[IO.Path]::GetFullPath($DeploymentPath)
$localRoot=[IO.Path]::GetFullPath((Join-Path $repoRoot '.local'))
if(-not $deployment.StartsWith($localRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid deployment path'}
$config=Get-Content -LiteralPath (Join-Path $deployment 'server.json') -Raw|ConvertFrom-Json
$shell=(Get-Command pwsh -ErrorAction Stop).Source
$launcher=Join-Path $PSScriptRoot 'Start-DocMindCpuServer.ps1'
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
foreach($role in @('Ingest','Watch','Preview')){
    $taskName='DocMind CPU '+$config.project_name+' ('+$role.ToLowerInvariant()+')'
    if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw "Task already exists: $taskName"}
}
foreach($role in @('Ingest','Watch','Preview')){
    $taskName='DocMind CPU '+$config.project_name+' ('+$role.ToLowerInvariant()+')'
    $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$launcher+'" -Action '+$role+' -DeploymentPath "'+$deployment+'"'
    $action=New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $repoRoot
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    if($StartNow){Start-ScheduledTask -TaskName $taskName}
    Write-Output ('Registered: '+$taskName)
}
Write-Output 'Tasks run in this logged-in Windows account. Pre-login/unattended logoff operation requires separate account/WSL validation.'
