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
$hash=[Security.Cryptography.SHA256]::Create()
try{$lockName=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($repoRoot.ToLowerInvariant())))).Replace('-','')}finally{$hash.Dispose()}
$installerMutex=[Threading.Mutex]::new($false,('Local\DocMindInstall-'+$lockName))
try{$acquired=$installerMutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$acquired=$true}
if(-not $acquired){$installerMutex.Dispose();throw '다른 설치 또는 폴더 설정 적용이 진행 중입니다. 해당 작업이 끝난 후 다시 실행해 주십시오.'}
try {
$prepared=Join-Path $repoRoot '.local/cpu-server'
if(-not $SourceFolders){$SourceFolders=Join-Path $repoRoot 'deployment/source-folders.txt'}
if(-not $SettingsArchive){$SettingsArchive=Join-Path $repoRoot 'deployment/install-inputs/docmind-deployed-20261019-settings.tar.enc'}
if(-not $RecoveryKey){$RecoveryKey=Join-Path $repoRoot 'deployment/install-inputs/recovery.key'}
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
    if($mode -eq 'wsl'){& wsl.exe -d $distribution --exec /usr/bin/docker compose version}else{& docker compose version}
    if($LASTEXITCODE -ne 0){throw 'Docker Compose v2를 준비해 주십시오. 이미지를 불러오는 방식이므로 Buildx와 소스 빌드는 필요하지 않습니다.'}
    $arguments=@((Join-Path $PSScriptRoot 'prepare_cpu_server.py'),'--source-folders',$SourceFolders,'--settings-archive',$SettingsArchive,'--recovery-key',$RecoveryKey,'--docker-mode',$mode)
    if($distribution){$arguments+=@('--wsl-distribution',$distribution)}
    & $runtime @arguments
    if($LASTEXITCODE -ne 0){throw 'Preparation failed. Resolve the reported environment/input problem before retrying.'}
}
$start=Join-Path $PSScriptRoot 'Start-DocMindCpuServer.ps1'
$cfg=Get-Content -LiteralPath (Join-Path $prepared 'server.json') -Raw -Encoding UTF8|ConvertFrom-Json
$roles=@('Ingest','Watch','Preview')
$existing=@($roles|ForEach-Object {Get-ScheduledTask -TaskName ('DocMind CPU '+$cfg.project_name+' ('+$_.ToLowerInvariant()+')') -ErrorAction SilentlyContinue})
$launcher=Join-Path $PSScriptRoot 'Start-DocMindCpuServer.ps1'
foreach($task in $existing){
    if(@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne (Get-Command pwsh).Source -or
       -not $task.Actions[0].Arguments.Contains('"'+$launcher+'"') -or -not $task.Actions[0].Arguments.Contains('"'+$prepared+'"')){
        throw '같은 이름의 다른 설치 작업이 있습니다. 기존 작업의 실행 경로를 확인해 주십시오.'
    }
}
$stopFile=Join-Path $prepared 'stop-workers'
[IO.File]::WriteAllText($stopFile,'Stop after the current document and cleanup complete.')
Write-Output '문서 폴더 설정을 적용하기 위해 현재 문서 처리와 임시 파일 정리가 끝나기를 기다립니다.'
$deadline=[DateTimeOffset]::UtcNow.AddMinutes(30)
while(@($existing|Where-Object {(Get-ScheduledTask -TaskName $_.TaskName).State -eq 'Running'}).Count){
    if([DateTimeOffset]::UtcNow -gt $deadline){throw '수집기 정리가 아직 완료되지 않았습니다. 기존 설정을 유지했습니다. 처리 중인 문서와 작업 상태를 확인한 뒤 다시 실행해 주십시오.'}
    Start-Sleep -Seconds 3
}
& $runtime (Join-Path $PSScriptRoot 'prepare_cpu_server.py') --refresh-sources --source-folders $SourceFolders --output $prepared
if($LASTEXITCODE -ne 0){throw '문서 폴더 설정을 적용하지 못했습니다. 폴더 주소와 접근 권한을 확인해 주십시오.'}
$worker=Get-Content -LiteralPath (Join-Path $prepared 'worker.json') -Raw -Encoding UTF8|ConvertFrom-Json
$signature=Get-AuthenticodeSignature -LiteralPath $worker.executable_path
if($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or $signature.SignerCertificate.Thumbprint -ne $worker.executable_signer_thumbprint){
    throw '복호화 프로그램의 승인된 서명을 확인하지 못했습니다. 설치된 uEncryptor2 버전과 인증서를 확인해 주십시오.'
}
foreach($step in @('LoadImages','Up','Bootstrap','Status')){& $start -Action $step -DeploymentPath $prepared}
Remove-Item -LiteralPath $stopFile -Force
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
} finally {$installerMutex.ReleaseMutex();$installerMutex.Dispose()}
