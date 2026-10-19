"""Verify/download PRIVATE prebuilt image parts and load them without a build."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
REPOSITORY='Dongmin103/docmind-private'

def docker_command(config):
    return ['wsl.exe','-d',config['wsl_distribution'],'--exec','/usr/bin/docker'] if config['docker_mode']=='wsl' else ['docker']

def image_matches(command,image):
    result=subprocess.run(command+['image','inspect','--format','{{.Id}}',image['tag']],capture_output=True,text=True,encoding='utf-8',errors='replace')
    return result.returncode==0 and result.stdout.strip()==image['id']

def checksum(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        while data:=source.read(4*1024*1024):digest.update(data)
    return digest.hexdigest()

class PrivateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,response,code,message,headers,newurl):
        parsed=urllib.parse.urlsplit(newurl)
        if parsed.scheme!='https' or parsed.hostname not in {'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}:
            raise ValueError('Unexpected image download redirect')
        redirected=super().redirect_request(request,response,code,message,headers,newurl)
        if parsed.hostname!='api.github.com':redirected.remove_header('Authorization')
        return redirected

def github_token():
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:return token
    result=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',
        capture_output=True,text=True,encoding='utf-8',errors='replace',cwd=ROOT,env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never'))
    fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or not fields.get('password'):
        raise ValueError('PRIVATE 이미지 다운로드 권한을 확인해 주십시오. 저장소를 받은 GitHub 계정의 Git 인증 또는 GH_TOKEN을 사용하며, 오프라인 설치는 이미지 분할 파일을 .local/images에 준비합니다.')
    return fields['password']

def api_json(opener,path,token):
    request=urllib.request.Request('https://api.github.com'+path,headers={'Authorization':'Bearer '+token,
        'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    with opener.open(request,timeout=90) as response:return json.load(response)

def valid_part(path,part):
    return path.is_file() and not path.is_symlink() and path.stat().st_size==part['bytes'] and checksum(path)==part['sha256']

def ensure_images(config,manifest,cache,offline=False):
    if manifest['repository']!=REPOSITORY or manifest['platform']!='linux/amd64':raise ValueError('Unexpected runtime image manifest')
    command=docker_command(config)
    architecture=subprocess.run(command+['info','--format','{{.Architecture}}'],capture_output=True,text=True,encoding='utf-8',errors='replace',check=True).stdout.strip()
    if architecture not in {'x86_64','amd64'}:raise ValueError('이 실행 이미지는 x64 Linux Docker용입니다. 서버 아키텍처를 확인해 주십시오.')
    if all(image_matches(command,image) for image in manifest['images']):
        print('준비된 Docker 이미지를 확인했습니다. 재빌드하지 않습니다.');return
    cache=cache.resolve()
    if not cache.is_relative_to((ROOT/'.local').resolve()):raise ValueError('Image cache must stay inside .local')
    cache.mkdir(parents=True,exist_ok=True)
    for part in manifest['parts']:
        if Path(part['name']).name!=part['name'] or not part['name'].startswith('docmind-images-'):raise ValueError('Invalid image part name')
    missing=[part for part in manifest['parts'] if not valid_part(cache/part['name'],part)]
    if missing:
        if offline:raise ValueError('오프라인 이미지 파일이 없거나 해시가 일치하지 않습니다: '+', '.join(p['name'] for p in missing))
        token=github_token();opener=urllib.request.build_opener(PrivateRedirect())
        repo=api_json(opener,'/repos/'+REPOSITORY,token)
        if repo.get('private') is not True:raise ValueError('Image repository must remain PRIVATE')
        release=api_json(opener,'/repos/'+REPOSITORY+'/releases/tags/'+manifest['release_tag'],token)
        assets={asset['name']:asset for asset in release['assets']}
        for index,part in enumerate(missing,1):
            asset=assets.get(part['name'])
            if not asset or asset['size']!=part['bytes']:raise ValueError('PRIVATE 이미지 파일을 찾을 수 없습니다: '+part['name'])
            target=cache/part['name'];temporary=target.with_name(target.name+'.partial')
            if target.is_symlink() or temporary.is_symlink():raise ValueError('Image cache symlinks are not supported')
            print(f"이미지 파일 다운로드 {index}/{len(missing)}: {part['name']}",flush=True)
            for attempt in range(3):
                try:
                    request=urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+'/releases/assets/'+str(asset['id']),
                        headers={'Authorization':'Bearer '+token,'Accept':'application/octet-stream','X-GitHub-Api-Version':'2022-11-28'})
                    received=0;next_progress=100*1024*1024
                    with opener.open(request,timeout=90) as response,temporary.open('wb') as output:
                        while data:=response.read(4*1024*1024):
                            output.write(data);received+=len(data)
                            if received>=next_progress:
                                print(f"  {part['name']}: {received//1024//1024}/{part['bytes']//1024//1024} MiB",flush=True)
                                next_progress+=100*1024*1024
                    if not valid_part(temporary,part):raise ValueError('Image checksum mismatch: '+part['name'])
                    temporary.replace(target);break
                except (OSError,urllib.error.URLError):
                    if attempt==2:raise ValueError('이미지 다운로드에 실패했습니다. 네트워크/저장소 권한을 확인한 후 다시 실행해 주십시오: '+part['name']) from None
                    time.sleep(2)
    print('Docker 이미지를 불러오는 중입니다. 소스 빌드와 모델 다운로드는 수행하지 않습니다.',flush=True)
    process=subprocess.Popen(command+['load'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    try:
        for index,part in enumerate(manifest['parts'],1):
            print(f"이미지 파일 전달 {index}/{len(manifest['parts'])}: {part['name']}",flush=True)
            with (cache/part['name']).open('rb') as source:
                while data:=source.read(4*1024*1024):process.stdin.write(data)
        process.stdin.close();output=process.stdout.read();code=process.wait()
    except BaseException:
        process.kill();process.wait();raise
    if code:raise ValueError('Docker 이미지 불러오기에 실패했습니다. 디스크 공간과 Docker 상태를 확인해 주십시오.')
    if not all(image_matches(command,image) for image in manifest['images']):raise ValueError('Loaded image IDs do not match the release manifest')
    print(f"Docker 이미지 {len(manifest['images'])}개를 확인했습니다.",flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=ROOT/'deployment/runtime-images.json')
    parser.add_argument('--cache',type=Path,default=ROOT/'.local/images');parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    ensure_images(json.loads(args.config.read_text(encoding='utf-8-sig')),json.loads(args.manifest.read_text(encoding='utf-8-sig')),args.cache,args.offline)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.SubprocessError) as error:raise SystemExit(str(error))
