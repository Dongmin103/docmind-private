"""Initialize this deployment's empty workspace; no old document/index import."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

def stable(*parts):return hashlib.sha256('\x1f'.join(parts).encode()).hexdigest()[:32]

def initialize(config):
    from api.db.db_models import (DB,User,Tenant,UserTenant,File,DocmindFolder,DocmindSource,DocmindProject,Knowledgebase,
        TenantModelProvider,TenantModelInstance,TenantModel)
    from api.apps.services.docmind_bootstrap_service import bootstrap_shared_workspace
    email=config['email'].strip().lower()
    shared_channel='internal-shared-workspace'
    with DB.connection_context(),DB.atomic():
        # Refuse to adopt an existing installation or anyone else's tenant.
        if User.select().where(User.email!=email).exists():raise ValueError('Fresh-server bootstrap refuses a database containing other users')
        user=User.get_or_none(User.email==email)
        if user is None:
            user=User.create(id=uuid4().hex,email=email,nickname='DOCMIND',password=None,
                access_token=uuid4().hex,login_channel=shared_channel,is_superuser=False,last_login_time=datetime.now(),language='Korean')
            Tenant.create(id=user.id,name='DOCMIND',llm_id='',embd_id='',img2txt_id='',rerank_id='',parser_ids='')
            UserTenant.create(id=uuid4().hex,user_id=user.id,tenant_id=user.id,invited_by=user.id,role='owner')
            file_id=uuid4().hex
            File.create(id=file_id,parent_id=file_id,tenant_id=user.id,created_by=user.id,name='/',type='folder',size=0,location='')
        else:
            # Keep the API's shared identity internal. Also accept an earlier
            # CPU bootstrap's own model binding so it can drop its login password.
            own_model=TenantModel.get_or_none(TenantModel.id==stable('docmind-cpu-model',user.id))
            if user.login_channel!=shared_channel and own_model is None:
                raise ValueError('Expected an internal shared workspace identity')
            if (DocmindProject.select().where(DocmindProject.tenant_id==user.id).exists()
                    or Knowledgebase.select().where(Knowledgebase.tenant_id==user.id).exists()) and own_model is None:
                raise ValueError('Refusing to adopt an internal account with an existing workspace')
            memberships=list(UserTenant.select().where(UserTenant.user_id==user.id))
            if len(memberships)!=1 or memberships[0].tenant_id!=user.id or memberships[0].role!='owner':
                raise ValueError('Internal account ownership differs from bootstrap')
        tenant=Tenant.get_by_id(user.id)
        provider_id=stable('docmind-cpu-provider',user.id);instance_id=stable('docmind-cpu-instance',user.id);model_id=stable('docmind-cpu-model',user.id)
        provider,_=TenantModelProvider.get_or_create(id=provider_id,defaults={'tenant_id':user.id,'provider_name':'OpenAI-API-Compatible'})
        if provider.tenant_id!=user.id:raise ValueError('Model provider ownership conflict')
        instance,_=TenantModelInstance.get_or_create(id=instance_id,defaults={'provider_id':provider_id,'instance_name':'local-gte',
            'api_key':config['gte_api_key'],'extra':json.dumps({'base_url':'http://gte:18882/v1'})})
        if instance.provider_id!=provider_id or instance.api_key!=config['gte_api_key']:raise ValueError('Embedding credentials differ from bootstrap; refusing silent rotation')
        model,_=TenantModel.get_or_create(id=model_id,defaults={'provider_id':provider_id,'instance_id':instance_id,'model_name':'gte-multilingual-base',
            'model_type':2,'extra':json.dumps({'max_tokens':8192})})
        if (model.provider_id,model.instance_id,model.model_name,model.model_type)!=(provider_id,instance_id,'gte-multilingual-base',2):
            raise ValueError('Existing embedding model configuration differs')
        if tenant.tenant_embd_id and tenant.tenant_embd_id!=model_id:raise ValueError('Existing embedding binding differs')
        if user.login_channel!=shared_channel:
            User.update(password=None,login_channel=shared_channel,is_superuser=False).where(User.id==user.id).execute()
        Tenant.update(embd_id=model_id,tenant_embd_id=model_id).where(Tenant.id==user.id).execute()
    # Service facades own their connection contexts. Do not call them inside an
    # outer transaction; stable IDs let each completed phase be retried safely.
    workspace=bootstrap_shared_workspace(user.id)
    with DB.connection_context(),DB.atomic():
        dataset=Knowledgebase.get_by_id(workspace['dataset_id'])
        if dataset.tenant_embd_id and dataset.tenant_embd_id!=model_id:raise ValueError('Existing dataset embedding differs')
        Knowledgebase.update(name='DOCMIND',embd_id=model_id,tenant_embd_id=model_id).where(Knowledgebase.id==dataset.id).execute()
        next_ordinal=max((folder.ordinal for folder in DocmindFolder.select(DocmindFolder.ordinal).where(
            DocmindFolder.project_id==workspace['project_id'])),default=-1)+1
        for source in config['sources']:
            folder_id=stable('docmind-cpu-source-folder',workspace['project_id'],source['source_id'])
            folder,created=DocmindFolder.get_or_create(id=folder_id,defaults={'project_id':workspace['project_id'],
                'slug':'source-'+source['source_id'],'display_name':source.get('display_name',source['source_id']),'ordinal':next_ordinal})
            if created:next_ordinal+=1
            if folder.project_id!=workspace['project_id']:raise ValueError('Folder ownership conflict')
            DocmindFolder.update(display_name=source.get('display_name',source['source_id'])).where(DocmindFolder.id==folder.id).execute()
            record,_=DocmindSource.get_or_create(id=source['source_id'],defaults={'project_id':workspace['project_id'],
                'display_name':source.get('display_name',source['source_id']),'default_folder_id':folder_id})
            if record.project_id!=workspace['project_id'] or record.default_folder_id!=folder_id:raise ValueError('Source ownership conflict')
            DocmindSource.update(enabled=True,display_name=source.get('display_name',source['source_id'])).where(DocmindSource.id==record.id).execute()
        active={source['source_id'] for source in config['sources']}
        for source_id in config.get('disable_source_ids',[]):
            if source_id in active:raise ValueError('A source cannot be active and paused at the same time')
            record=DocmindSource.get_or_none(DocmindSource.id==source_id)
            if record is None:continue
            if record.project_id!=workspace['project_id']:raise ValueError('Paused source ownership conflict')
            DocmindSource.update(enabled=False).where(DocmindSource.id==source_id).execute()
    return {'ready':True,'sources':len(config['sources']),'embedding_dimensions':768,'old_documents_imported':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,default=Path('/run/docmind-bootstrap.json'))
    args=parser.parse_args();config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    print(json.dumps(initialize(config)))

if __name__=='__main__':main()
