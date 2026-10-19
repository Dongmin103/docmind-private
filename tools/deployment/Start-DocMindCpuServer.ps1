[CmdletBinding()]
param(
    [ValidateSet('Build','DownloadModels','Up','Bootstrap','Status','IngestOnce','Ingest','Watch','Preview','Stop')]
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
switch($Action){
    Build {Invoke-DocMindDocker ($base+@('build','api','gte','kordoc-parser','preview-processor'))}
    DownloadModels {Invoke-DocMindDocker ($base+@('run','--rm','--no-deps','--user','0:0','-e','HF_HUB_OFFLINE=0','-e','TRANSFORMERS_OFFLINE=0','--entrypoint','python','gte','/opt/gte/download_model.py'))}
    Up {Invoke-DocMindDocker ($base+@('up','-d','--wait','--wait-timeout','600'))}
    Bootstrap {Invoke-DocMindDocker ($base+@('exec','-T','api','python','/opt/docmind/bootstrap_cpu_workspace.py'))}
    Status {Invoke-DocMindDocker ($base+@('ps'))}
    Stop {Invoke-DocMindDocker ($base+@('stop'))}
    IngestOnce { & (Join-Path $repoRoot 'tools/windows/Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $deployment 'worker.json') -Once }
    Ingest { & (Join-Path $repoRoot 'tools/windows/Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $deployment 'worker.json') }
    Watch { & (Join-Path $repoRoot 'tools/windows/Watch-DocMindEncryptedSources.ps1') -ConfigPath (Join-Path $deployment 'worker.json') }
    Preview { & (Join-Path $repoRoot 'tools/windows/Start-DocMindUEncryptorHostWorker.ps1') -ConfigPath (Join-Path $deployment 'preview-worker.json') -Purpose preview }
}
