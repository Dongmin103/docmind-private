[CmdletBinding()]
param(
    [string]$SourceFolders,
    [string]$SettingsArchive,
    [string]$RecoveryKey,
    [string]$Python='python',
    [switch]$SkipWorkers
)
$ErrorActionPreference='Stop'
if(-not $IsWindows){throw 'This launcher requires Windows. The deployment agent must review README.md for other host layouts and the required Windows decryptor worker.'}
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$prepared=Join-Path $repoRoot '.local/cpu-server'
if(-not $SourceFolders){$SourceFolders=Join-Path $repoRoot 'deployment/source-folders.txt'}
if(-not $SettingsArchive){$SettingsArchive=Join-Path $repoRoot '.local/install-inputs/docmind-deployed-20261019-settings.tar.enc'}
if(-not $RecoveryKey){$RecoveryKey=Join-Path $repoRoot '.local/install-inputs/recovery.key'}
if(-not (Get-Command $Python -ErrorAction SilentlyContinue)){throw 'Deployment agent: install Python 3.11+ as described in README.md.'}
& $Python -c 'import sys; assert sys.version_info >= (3,11)'
if($LASTEXITCODE -ne 0){throw 'Python 3.11+ is required.'}
# Dependencies stay inside this checkout; do not alter the host Python packages.
$venv=Join-Path $repoRoot '.local/installer-python'
$runtime=Join-Path $venv 'Scripts/python.exe'
if(-not(Test-Path -LiteralPath $runtime)){
    & $Python -m venv $venv
    if($LASTEXITCODE -ne 0){throw 'Cannot create the isolated installer Python environment.'}
}
& $runtime -c 'import cryptography' 2>$null
if($LASTEXITCODE -ne 0){
    & $runtime -m pip install 'cryptography==50.0.2'
    if($LASTEXITCODE -ne 0){throw 'Cannot install cryptography. Deployment agent: check the package network or use the approved offline wheel mirror.'}
}
if(-not(Test-Path -LiteralPath $prepared)){
    foreach($path in @($SourceFolders,$SettingsArchive,$RecoveryKey)){
        if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Deployment input missing: $path. See README.md; API keys must be supplied unchanged through the encrypted settings archive."}
    }
    # Probe available Linux engines instead of assuming a distribution name.
    $mode='';$distribution=''
    if(Get-Command wsl.exe -ErrorAction SilentlyContinue){
        $distros=@(& wsl.exe --list --quiet 2>$null)|ForEach-Object {($_ -replace "`0",'').Trim()}|Where-Object {$_}
        foreach($candidate in $distros){
            $kind=& wsl.exe -d $candidate --exec /usr/bin/docker info --format '{{.OSType}}' 2>$null
            if($LASTEXITCODE -eq 0 -and ([string]$kind).Trim() -eq 'linux'){$mode='wsl';$distribution=$candidate;break}
        }
    }
    if(-not $mode -and (Get-Command docker -ErrorAction SilentlyContinue)){
        $kind=& docker info --format '{{.OSType}}' 2>$null
        if($LASTEXITCODE -eq 0 -and ([string]$kind).Trim() -eq 'linux'){$mode='desktop'}
    }
    if(-not $mode){throw 'No accessible Linux Docker engine found. Deployment agent: assess Windows/WSL/Docker using README.md before continuing.'}
    foreach($check in @(@('compose','version'),@('buildx','version'))){
        if($mode -eq 'wsl'){& wsl.exe -d $distribution --exec /usr/bin/docker @check}else{& docker @check}
        if($LASTEXITCODE -ne 0){throw 'Deployment agent: prepare Compose v2 and Buildx for the detected engine; see README.md. The installer does not change the Docker daemon.'}
    }
    $arguments=@((Join-Path $PSScriptRoot 'prepare_cpu_server.py'),'--source-folders',$SourceFolders,'--settings-archive',$SettingsArchive,'--recovery-key',$RecoveryKey,'--docker-mode',$mode)
    if($distribution){$arguments+=@('--wsl-distribution',$distribution)}
    & $runtime @arguments
    if($LASTEXITCODE -ne 0){throw 'Preparation failed. Resolve the reported environment/input problem before retrying.'}
}
$start=Join-Path $PSScriptRoot 'Start-DocMindCpuServer.ps1'
foreach($step in @('Build','DownloadModels','Up','Bootstrap','Status')){& $start -Action $step -DeploymentPath $prepared}
$cfg=Get-Content -LiteralPath (Join-Path $prepared 'server.json') -Raw -Encoding UTF8|ConvertFrom-Json
if(-not $SkipWorkers){
    $existing=@(@('Ingest','Watch','Preview')|ForEach-Object {Get-ScheduledTask -TaskName ('DocMind CPU '+$cfg.project_name+' ('+$_.ToLowerInvariant()+')') -ErrorAction SilentlyContinue})
    if($existing.Count -eq 0){& (Join-Path $PSScriptRoot 'Register-DocMindCpuWorkers.ps1') -DeploymentPath $prepared -StartNow}
    elseif($existing.Count -ne 3){throw 'Deployment agent: scheduled worker registration is incomplete; review the three project tasks before retrying.'}
    else{
        $launcher=Join-Path $PSScriptRoot 'Start-DocMindCpuServer.ps1'
        foreach($task in $existing){
            if(@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne (Get-Command pwsh).Source -or
               -not $task.Actions[0].Arguments.Contains('"'+$launcher+'"') -or -not $task.Actions[0].Arguments.Contains('"'+$prepared+'"')){
                throw 'A matching task name belongs to another launcher. Deployment agent must review it before retrying.'
            }
        }
        foreach($task in $existing){Start-ScheduledTask -TaskName $task.TaskName}
        Write-Output 'The three existing Windows worker tasks were started.'
    }
}
Write-Output ('DOCMIND CPU initialized: http://127.0.0.1:'+ $cfg.web_port)
Write-Output 'Open the URL to enter the shared DOCMIND workspace directly. No email or password entry is required.'
