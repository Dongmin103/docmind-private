[CmdletBinding()]
param(
    [ValidateSet('LoadImages','Up','Bootstrap','Status','IngestOnce','Ingest','Watch','Preview','Stop')]
    [string]$Action='Status',
    [string]$DeploymentPath
)
$ErrorActionPreference='Stop'
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if(-not $DeploymentPath){$DeploymentPath=Join-Path $repoRoot '.local/cpu-server'}
$deployment=[IO.Path]::GetFullPath($DeploymentPath)
$localRoot=[IO.Path]::GetFullPath((Join-Path $repoRoot '.local'))
if(-not $deployment.StartsWith($localRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Deployment must be inside this checkout .local'}
$config=Get-Content -LiteralPath (Join-Path $deployment 'server.json') -Raw -Encoding UTF8|ConvertFrom-Json
$compose=Join-Path $deployment 'compose.json'
if($config.docker_mode -eq 'wsl'){
    if($compose -notmatch '^([A-Za-z]):[\\/](.*)$'){throw 'WSL deployment must be on a local Windows drive'}
    $compose='/mnt/'+$Matches[1].ToLower()+'/'+($Matches[2] -replace '\\','/')
}
function Invoke-DocMindDocker([string[]]$DockerArguments){
    if($config.docker_mode -eq 'wsl'){
        & wsl.exe -d $config.wsl_distribution --exec /usr/bin/docker @DockerArguments
    }else{ & docker @DockerArguments }
    if($LASTEXITCODE -ne 0){throw "Docker action failed ($LASTEXITCODE). No credentials were printed by this launcher."}
}
$base=@('compose','--project-name',[string]$config.project_name,'-f',$compose)
$stopFile=Join-Path $deployment 'stop-workers'
if($Action -in @('IngestOnce','Ingest','Watch','Preview') -and (Test-Path -LiteralPath $stopFile)){throw '폴더 설정 적용을 위해 수집기가 정지되어 있습니다. 설치 실행 파일을 완료해 주십시오.'}
switch($Action){
    LoadImages {
        & (Join-Path $repoRoot '.local/installer-python/Scripts/python.exe') (Join-Path $PSScriptRoot 'runtime_images.py') --config (Join-Path $deployment 'server.json')
        if($LASTEXITCODE -ne 0){throw '이미지 준비에 실패했습니다. 안내된 권한 또는 네트워크 상태를 확인해 주십시오.'}
    }
    Up {Invoke-DocMindDocker ($base+@('up','--no-build','--pull','never','-d','--wait','--wait-timeout','600'))}
    Bootstrap {Invoke-DocMindDocker ($base+@('exec','-T','api','python','/opt/docmind/bootstrap_cpu_workspace.py'))}
    Status {Invoke-DocMindDocker ($base+@('ps'))}
    Stop {Invoke-DocMindDocker ($base+@('stop'))}
    IngestOnce { & (Join-Path $repoRoot 'tools/windows/Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $deployment 'worker.json') -Once -StopFile $stopFile }
    Ingest { & (Join-Path $repoRoot 'tools/windows/Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $deployment 'worker.json') -StopFile $stopFile }
    Watch { & (Join-Path $repoRoot 'tools/windows/Watch-DocMindEncryptedSources.ps1') -ConfigPath (Join-Path $deployment 'worker.json') -StopFile $stopFile }
    Preview { & (Join-Path $repoRoot 'tools/windows/Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $deployment 'preview-worker.json') -Purpose preview -StopFile $stopFile }
}
