"""Generate a new CPU-only deployment from encrypted credentials, never old data."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import tarfile
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
MODEL_REVISION='9bbca17d9273fd0d03d5725c7a4b0f6b45142062'
CODE_REVISION='40ced75c3017eb27626c9d4ea981bde21a2662f4'
MAGIC=b'DOCMIND-RECOVERY-AES256GCM-V1\n'

def decrypt_entries(archive: Path,key_path: Path)->dict[str,bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key=key_path.read_bytes();data=archive.read_bytes()
    if len(key)!=32 or not data.startswith(MAGIC):raise ValueError('Invalid settings archive or recovery key')
    nonce=data[len(MAGIC):len(MAGIC)+12]
    plaintext=AESGCM(key).decrypt(nonce,data[len(MAGIC)+12:],MAGIC)
    with tarfile.open(fileobj=io.BytesIO(plaintext),mode='r:gz') as source:
        entries={}
        for member in source:
            if not member.isfile() or member.name.startswith(('/', '\\')) or '..' in Path(member.name).parts:
                raise ValueError('Unsafe settings archive member')
            if member.name in entries:raise ValueError('Duplicate settings archive member')
            entries[member.name]=source.extractfile(member).read()
    manifest=json.loads(entries['manifest.json'])
    for item in manifest['entries']:
        content=entries[item['path']]
        if len(content)!=item['bytes'] or hashlib.sha256(content).hexdigest()!=item['sha256']:
            raise ValueError('Settings archive integrity mismatch')
    return entries

def validate_config(config:dict,check_paths=True):
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,40}',config['project_name']):raise ValueError('Invalid project_name')
    if config.get('docker_mode') not in {'wsl','desktop'}:raise ValueError('docker_mode must be wsl or desktop')
    if not config.get('backup_exclusion_acknowledged'):raise ValueError('Exclude plaintext_root and its -preview sibling from backup, then set backup_exclusion_acknowledged=true')
    if not config.get('workspace_email') or '@' not in config['workspace_email']:raise ValueError('workspace_email required')
    if type(config.get('cpu_threads',2)) is not int or not 1<=config.get('cpu_threads',2)<=128:raise ValueError('cpu_threads must be 1..128')
    for key in ['web_port','api_port']:
        if type(config[key]) is not int or not 1024<=config[key]<=65535:raise ValueError('Invalid port')
    if config['web_port']==config['api_port']:raise ValueError('Ports must differ')
    if not config.get('sources'):raise ValueError('At least one explicit source root is required')
    ids=set();roots=[]
    required_paths=['plaintext_root','decrypt_executable']
    if any(not Path(config[key]).is_absolute() for key in required_paths):raise ValueError('Deployment paths must be absolute')
    work=Path(config['plaintext_root']).resolve()
    if not work.name:raise ValueError('plaintext_root must be a dedicated directory')
    preview=work.with_name(work.name+'-preview')
    for source in config['sources']:
        identifier=source['source_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',identifier) or identifier in ids:raise ValueError('Invalid or duplicate source_id')
        ids.add(identifier)
        if not Path(source['root']).is_absolute():raise ValueError('Source roots must be absolute')
        root=Path(source['root']).resolve()
        if check_paths and not root.is_dir():raise ValueError('Source root is not accessible: '+str(root))
        if any(path==root or path.is_relative_to(root) or root.is_relative_to(path) for path in [work,preview]):raise ValueError('Plaintext and encrypted roots must be separate')
        if any(root==other or root.is_relative_to(other) or other.is_relative_to(root) for other in roots):raise ValueError('Source roots must not overlap')
        roots.append(root)
    if check_paths and not Path(config['decrypt_executable']).is_file():raise ValueError('decrypt_executable is not accessible')
    answer=config.get('answer_base_url','')
    if answer and (urlsplit(answer).scheme not in {'http','https'} or urlsplit(answer).username):raise ValueError('Invalid answer_base_url')

def docker_path(path:Path,config:dict)->str:
    value=str(path.resolve()).replace('\\','/')
    if config['docker_mode']=='wsl':
        if not re.match(r'^[A-Za-z]:/',value):raise ValueError('WSL deployment files must be on a local Windows drive')
        return '/mnt/'+value[0].lower()+value[2:]
    return value

def build_compose(config:dict,output:Path,environment:dict)->dict:
    def mount(source,target,readonly=True):
        return {'type':'bind','source':docker_path(Path(source),config),'target':target,'read_only':readonly}
    def base(image,memory):return {'image':image,'restart':'unless-stopped','mem_limit':memory,'networks':['docmind']}
    def health(command):return {'test':command,'interval':'10s','timeout':'5s','retries':60,'start_period':'30s'}
    # Compose interpolates dollar signs even in JSON strings.
    env={k:str(v).replace('$','$$') for k,v in environment.items()}
    services={}
    services['mysql']=base('mysql:8.0.40','1g')|{'environment':{'MYSQL_ROOT_PASSWORD':env['MYSQL_PASSWORD'],'MYSQL_DATABASE':'docmind'},
        'command':['--character-set-server=utf8mb4','--collation-server=utf8mb4_unicode_ci'],
        'volumes':['mysql-data:/var/lib/mysql'],'healthcheck':health(['CMD','mysqladmin','ping','-h','127.0.0.1'])}
    services['es01']=base('elasticsearch:8.11.3','2g')|{'environment':{'discovery.type':'single-node','xpack.security.enabled':'true',
        'xpack.security.http.ssl.enabled':'false','xpack.security.transport.ssl.enabled':'false','ELASTIC_PASSWORD':env['ELASTIC_PASSWORD'],
        'ES_JAVA_OPTS':'-Xms512m -Xmx512m'},'volumes':['es-data:/usr/share/elasticsearch/data'],
        'healthcheck':health(['CMD-SHELL','curl -fsS -u "elastic:$$ELASTIC_PASSWORD" http://127.0.0.1:9200/_cluster/health >/dev/null'])}
    services['redis']=base('valkey/valkey:8','256m')|{'environment':{'REDIS_PASSWORD':env['REDIS_PASSWORD']},
        'command':['redis-server','--requirepass',env['REDIS_PASSWORD']],'volumes':['redis-data:/data'],
        'healthcheck':health(['CMD-SHELL','redis-cli --no-auth-warning -a "$$REDIS_PASSWORD" ping | grep -q PONG'])}
    services['minio']=base('pgsty/silo:RELEASE.2026-08-06T00-00-00Z','512m')|{'environment':{'MINIO_ROOT_USER':env['MINIO_USER'],'MINIO_ROOT_PASSWORD':env['MINIO_PASSWORD']},
        'command':['server','/data'],'volumes':['minio-data:/data'],
        'healthcheck':health(['CMD','curl','-fsS','http://127.0.0.1:9000/minio/health/live'])}
    services['gte']=base('docmind-gte:cpu','6g')|{'build':{'context':docker_path(ROOT/'tools/local_embedding',config),'dockerfile':'Containerfile.cpu'},
        'volumes':[mount(output/'gte.json','/run/config/service.json'),'gte-models:/models'],
        'cpus':config.get('cpu_threads',2)}
    services['kordoc-parser']=base('docmind-kordoc:cpu','2g')|{'build':{'context':docker_path(ROOT/'app/parser_services/kordoc',config),'dockerfile':'Containerfile'},
        'environment':{'KORDOC_OFFLINE':'1','KORDOC_REQUIRE_OCR':'0','KORDOC_WORKERS':'1'},
        'read_only':True,'tmpfs':['/tmp:rw,nosuid,nodev,size=1g']}
    services['preview-processor']=base('docmind-preview:cpu','512m')|{'build':{'context':docker_path(ROOT/'app/parser_services',config),'dockerfile':'preview/Containerfile'},
        'environment':{'DOCMIND_PREVIEW_ROOT':'/run/docmind-previews','HOME':'/run/docmind-previews/processor-home','TMPDIR':'/run/docmind-previews/processor-tmp'},
        'volumes':['preview-memory:/run/docmind-previews'],'read_only':True,'tmpfs':['/tmp:rw,nosuid,size=64m']}
    api_env=env|{'MYSQL_DBNAME':'docmind','MYSQL_HOST':'mysql','MYSQL_PORT':'3306','ES_HOST':'es01','ES_PORT':'9200',
        'REDIS_HOST':'redis','REDIS_PORT':'6379','MINIO_HOST':'minio','MINIO_PORT':'9000','DB_TYPE':'mysql','DOC_ENGINE':'elasticsearch','STORAGE_IMPL':'MINIO',
        'DEVICE':'cpu','API_PROXY_SCHEME':'python','DOCMIND_OCR_ENABLED':'0','EMBEDDING_BATCH_SIZE':'1','EMBEDDING_BATCH_CONCURRENCY':'1',
        'DOCMIND_SHARED_WORKSPACE_ENABLED':'1','DOCMIND_SHARED_USER_EMAIL':config['workspace_email'],'DOCMIND_CATALOG_DB_PRIMARY_ENABLED':'1',
        'DOCMIND_GENERATIONLESS_E2E_ENABLED':'1','DOCMIND_E2E_BOOTSTRAP_ONLY':'0',
        'PARSER_PLATFORM_ENABLED':'1','PARSER_PLATFORM_INTEGRATION_READY':'1','PARSER_PLATFORM_KORDOC_URL':'http://kordoc-parser:8095',
        'PARSER_PLATFORM_OFFICE_ENABLED':'1','PARSER_PLATFORM_HWP_ENABLED':'1','KORDOC_CLIENT_CONCURRENCY':'1','DOCMIND_INGESTION_DOCUMENT_CONCURRENCY':'1',
        'DOCMIND_PREVIEW_ENABLED':'1','DOCMIND_PREVIEW_ROOT':'/run/docmind-previews','DOCMIND_PREVIEW_PROCESSOR_URL':'http://preview-processor:8090',
        'DOCMIND_EPHEMERAL_PARSER_ROOT':'/run/docmind-ephemeral-parser','JINA_API_KEY_FILE':'/run/secrets/jina-api-key',
        'DOCMIND_HOST_WORKER_HMAC_SECRET_FILE':'/run/secrets/docmind-host-worker-hmac','TE_RUN_MODE':'0','TZ':'Asia/Seoul'}
    for suffix in ['PDF','DOCX','EXCEL','PPTX','HWP']:api_env['PARSER_PLATFORM_KORDOC_'+suffix+'_ENABLED']='1'
    api_env.pop('DOCMIND_SHARED_USER_ID',None)
    if config.get('answer_base_url'):api_env['DOCMIND_ANSWER_BASE_URL']=config['answer_base_url'].replace('$','$$')
    if api_env.get('DOCMIND_ANSWER_PIPELINE')=='reviewed_v1':api_env['DOCMIND_REVIEWED_ALLOWED_BASE_URL']=api_env.get('DOCMIND_ANSWER_BASE_URL','').rstrip('/')
    services['api']=base('docmind:cpu','4g')|{'build':{'context':docker_path(ROOT/'app',config),'dockerfile':'Dockerfile'},
        'environment':api_env,'depends_on':{name:{'condition':'service_healthy'} for name in ['mysql','es01','redis','minio','gte','kordoc-parser']},
        'ports':[f"127.0.0.1:{config['web_port']}:80",f"127.0.0.1:{config['api_port']}:9380"],
        'volumes':[mount(output/'secrets/jina-api.key','/run/secrets/jina-api-key'),mount(output/'secrets/worker-hmac.key','/run/secrets/docmind-host-worker-hmac'),
            mount(output/'secrets/private.pem','/ragflow/conf/private.pem'),mount(output/'secrets/public.pem','/ragflow/conf/public.pem'),
            mount(output/'secrets/system_settings.json','/ragflow/conf/system_settings.json'),mount(output/'bootstrap.json','/run/docmind-bootstrap.json'),
            mount(ROOT/'tools/deployment/bootstrap_cpu_workspace.py','/opt/docmind/bootstrap_cpu_workspace.py'),'api-logs:/ragflow/logs','preview-memory:/run/docmind-previews'],
        'tmpfs':['/run/docmind-ephemeral-parser:rw,nosuid,nodev,mode=0700,size=512m'],
        'healthcheck':health(['CMD','python','-c','import socket; socket.create_connection(("127.0.0.1",9380),3).close()'])}
    return {'name':config['project_name'],'services':services,'networks':{'docmind':{}},
        'volumes':{name:{} for name in ['mysql-data','es-data','redis-data','minio-data','gte-models','api-logs']}|{
            'preview-memory':{'driver_opts':{'type':'tmpfs','device':'tmpfs','o':'size=134217728,mode=1777'}}}}

def prepare(config:dict,entries:dict[str,bytes],output:Path):
    validate_config(config)
    if output.exists():raise FileExistsError('Output exists; edit existing configuration instead of overwriting credentials')
    api=json.loads(entries['docker/api-inspect.json'])
    env=dict(value.split('=',1) for value in api['Config']['Env'])
    # Keep API/model configuration, not unrelated image/build metadata or old host paths.
    env={k:v for k,v in env.items() if k.startswith(('DOCMIND_ANSWER_','DOCMIND_REVIEWED_','PARSER_PLATFORM_')) or k in ['MYSQL_PASSWORD','ELASTIC_PASSWORD','REDIS_PASSWORD','MINIO_USER','MINIO_PASSWORD','DOCMIND_HOST_WORKER_KEY_ID']}
    for k in ['MYSQL_PASSWORD','ELASTIC_PASSWORD','REDIS_PASSWORD','MINIO_USER','MINIO_PASSWORD']:
        if not env.get(k):raise ValueError('Required archived setting missing: '+k)
    gte=json.loads(entries['host-config/gte.json'])
    gte.update(device='cpu',cpu_threads=config.get('cpu_threads',2),short_batch=1,bind='0.0.0.0',port=18882,
        model_path=f'/models/huggingface/hub/models--Alibaba-NLP--gte-multilingual-base/snapshots/{MODEL_REVISION}',code_revision=CODE_REVISION)
    def find_suffix(name):
        found=[content for path,content in entries.items() if path.startswith('docker/bind-files/') and Path(path).name.endswith(name)]
        if len(found)!=1:raise ValueError('Required secret mount missing or ambiguous: '+name)
        return found[0]
    # Validate all inputs before creating any output.
    jina=find_suffix('jina-api.key');hmac=find_suffix('host-worker-hmac.key')
    for name in ['private.pem','public.pem','system_settings.json']:
        if 'api/conf/'+name not in entries:raise ValueError('Archived authentication configuration missing')
    ingest_seed=json.loads(entries['host-config/ingest.json'])
    preview_seed=json.loads(entries.get('host-config/preview.json',entries['host-config/ingest.json']))
    for seed in [ingest_seed,preview_seed]:
        if seed.get('key_id')!=env.get('DOCMIND_HOST_WORKER_KEY_ID','windows-host-1'):raise ValueError('Archived worker and API key IDs differ')
    work=Path(config['plaintext_root']).resolve()
    preview_work=work.with_name(work.name+'-preview')
    protected=[work,preview_work,*[Path(source['root']).resolve() for source in config['sources']]]
    if any(output==path or output.is_relative_to(path) or path.is_relative_to(output) for path in protected):
        raise ValueError('Deployment settings and source/plaintext roots must be separate')
    output.mkdir(parents=True);(output/'secrets').mkdir()
    if os.name=='nt':
        import subprocess
        identity=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True).split(',')[-1].strip().strip('"')
        subprocess.run(['icacls',str(output),'/inheritance:r','/grant:r',f'*{identity}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL)
    def write_json(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'secrets/jina-api.key').write_bytes(jina);(output/'secrets/worker-hmac.key').write_bytes(hmac)
    for name in ['private.pem','public.pem','system_settings.json']:(output/'secrets'/name).write_bytes(entries['api/conf/'+name])
    write_json('gte.json',gte)
    password=secrets.token_urlsafe(24);(output/'admin-password.txt').write_text(password,encoding='utf-8')
    write_json('bootstrap.json',{'email':config['workspace_email'],'password':password,'gte_api_key':gte['api_key'],'sources':config['sources']})
    overrides=dict(api_base_uri=f"http://127.0.0.1:{config['api_port']}",shared_secret_file=str(output/'secrets/worker-hmac.key'),
        executable_path=str(Path(config['decrypt_executable']).resolve()),executable_sha256=hashlib.sha256(Path(config['decrypt_executable']).read_bytes()).hexdigest(),
        work_root=str(Path(config['plaintext_root']).resolve()),cleanup_receipt_root=str(output/'cleanup-receipts'),decrypt_control_root=str(output/'decrypt-control'),
        sync_state_root=str(output/'sync-state'),sources=[{'source_id':s['source_id'],'root':str(Path(s['root']).resolve())} for s in config['sources']],
        require_bitlocker=config.get('require_bitlocker',True),backup_exclusion_acknowledged=True)
    cfg=ingest_seed|overrides|dict(worker_id='docmind-cpu-worker',preview_enabled=False)
    write_json('worker.json',cfg)
    preview=preview_seed|overrides|dict(worker_id='docmind-cpu-preview',preview_enabled=True,work_root=str(preview_work),
        cleanup_receipt_root=str(output/'preview-cleanup-receipts'))
    write_json('preview-worker.json',preview)
    write_json('compose.json',build_compose(config,output,env))
    write_json('server.json',config)
    print(json.dumps({'prepared':True,'output':str(output),'device':'cpu','ocr_enabled':False,'sources':len(config['sources']),'old_index_data_imported':False}))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--settings-archive',type=Path,required=True);parser.add_argument('--recovery-key',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'.local/cpu-server')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'.local'):raise ValueError('Output must stay inside this checkout .local directory')
    config=json.loads(args.config.read_text(encoding='utf-8-sig'));validate_config(config)
    prepare(config,decrypt_entries(args.settings_archive,args.recovery_key),output)

if __name__=='__main__':
    try:main()
    except (ValueError,FileNotFoundError,FileExistsError) as exc:raise SystemExit(str(exc))
