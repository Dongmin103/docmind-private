[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ServerConfig,
    [Parameter(Mandatory=$true)][string]$SettingsArchive,
    [Parameter(Mandatory=$true)][string]$RecoveryKey,
    [string]$Python='python',
    [switch]$StartWorkers
)
$ErrorActionPreference='Stop'
if(-not $IsWindows){throw 'This installer requires Windows with the approved uEncryptor2 executable.'}
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$prepared=Join-Path $repoRoot '.local/cpu-server'
foreach($path in @($ServerConfig,$SettingsArchive,$RecoveryKey)){
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw "Required input file missing: $path"}
}
if(-not (Get-Command $Python -ErrorAction SilentlyContinue)){throw 'Install Python 3.11 or newer.'}
& $Python -c 'import sys,cryptography; assert sys.version_info >= (3,11)'
if($LASTEXITCODE -ne 0){throw 'Python 3.11+ and cryptography are required. Run: python -m pip install cryptography'}
$cfg=Get-Content -LiteralPath $ServerConfig -Raw -Encoding UTF8|ConvertFrom-Json
if($cfg.docker_mode -eq 'wsl'){
    $engineType=& wsl.exe -d $cfg.wsl_distribution --exec /usr/bin/docker info --format '{{.OSType}}'
}else{$engineType=& docker info --format '{{.OSType}}'}
if($LASTEXITCODE -ne 0 -or ([string]$engineType).Trim() -ne 'linux'){throw 'Docker Linux engine is not available for this Windows account.'}
foreach($check in @(@('compose','version'),@('buildx','version'))){
    if($cfg.docker_mode -eq 'wsl'){
        & wsl.exe -d $cfg.wsl_distribution --exec /usr/bin/docker @check
    }else{& docker @check}
    if($LASTEXITCODE -ne 0){throw 'Install Docker Compose v2 and the Docker Buildx plugin in the selected Docker environment before building DOCMIND.'}
}
if(Test-Path -LiteralPath $prepared){throw 'Deployment already prepared. Use Start-DocMindCpuServer.ps1 to resume; installation never overwrites keys or volumes.'}
& $Python (Join-Path $PSScriptRoot 'prepare_cpu_server.py') --config $ServerConfig --settings-archive $SettingsArchive --recovery-key $RecoveryKey
if($LASTEXITCODE -ne 0){throw 'Preparation failed.'}
$start=Join-Path $PSScriptRoot 'Start-DocMindCpuServer.ps1'
foreach($step in @('Build','DownloadModels','Up','Bootstrap','Status')){& $start -Action $step -DeploymentPath $prepared}
if($StartWorkers){& (Join-Path $PSScriptRoot 'Register-DocMindCpuWorkers.ps1') -DeploymentPath $prepared -StartNow}
Write-Output ('DOCMIND CPU initialized: http://127.0.0.1:'+ $cfg.web_port)
Write-Output ('Login email: '+$cfg.workspace_email)
Write-Output ('Initial password file: '+(Join-Path $prepared 'admin-password.txt'))
