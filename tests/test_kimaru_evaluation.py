"""Fake providers and private temporary ledgers; never live model/Keychain calls."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from banto import broker
from banto.guard import CostGuard, BudgetExceededError
from banto import kimaru_evaluation as e

SHA='a'*40

def write_private(path,value):
    path.write_text(json.dumps(value));path.chmod(0o600)

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setattr(broker,'IN_SERVICE',True)
    p=dict(contract=e.CONTRACT,candidate=SHA,batchId='c'*32,limitUsd='50.00',expiresAt=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),ownerApproved=True,syntheticOnly=True,geminiPaidTierConfirmed=True,rates=e.RATES)
    write_private(tmp_path/'kimaru-r0-policy.json',p)
    write_private(tmp_path/'kimaru-r0-ledger.json',dict(contract=e.CONTRACT,policyHash=e.digest(p),entries=[]))
    cfg=tmp_path/'budget.json';write_private(cfg,dict(monthly_limit_usd=50,hold_timeout_hours=1,pricing={},providers={},provider_limits={},model_limits={}))
    guard=CostGuard(config_path=str(cfg),data_dir=str(tmp_path/'data'),strict_ledger=True)
    return tmp_path,p,guard

def payload(provider='azure'):
    if provider=='azure':
        return dict(model='gpt-6-sol',store=False,instructions='Synthetic draft',input=[dict(role='user',content=[dict(type='input_text',text='Synthetic input')])],reasoning={'effort':'medium'},max_output_tokens=4096,text={'format':{'type':'json_schema','name':'test_schema','strict':True,'schema':{'type':'object'}}})
    return dict(systemInstruction={'parts':[{'text':'Synthetic polish'}]},contents=[{'role':'user','parts':[{'text':'Synthetic prose'}]}],generationConfig=dict(responseMimeType='application/json',responseSchema={'type':'OBJECT'},maxOutputTokens=4096,thinkingConfig={'thinkingLevel':'low'}))

class Transport:
    def __init__(self,directory,guard,result=None,error=None): self.calls=[];self.directory=directory;self.guard=guard;self.result=result;self.error=error
    def prepare(self,provider): self.calls.append(('prepare',provider))
    def send(self,provider,body):
        self.calls.append(('send',provider))
        ledger=e.read_json(self.directory/'kimaru-r0-ledger.json');assert ledger['entries'][-1]['state']=='RESERVED'
        entries=self.guard._load_usage()['entries'];assert entries[-1]['status']=='hold' and entries[-1]['durable'] is True
        if self.error: raise self.error
        if self.result is not None: return self.result
        if provider=='azure': return {'model':'gpt-6-sol-2026-09-22','usage':{'input_tokens':100,'output_tokens':200,'input_tokens_details':{'cached_tokens':50}},'status':'completed','output':[]}
        return {'modelVersion':e.GEMINI_MODEL,'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':120,'thoughtsTokenCount':80,'cachedContentTokenCount':50,'totalTokenCount':300},'candidates':[]}

def run(env,transport,provider='azure',request='1'*32,case='office-guideline',body=None):
    directory,p,g=env
    return e.generate(provider,payload(provider) if body is None else body,request,case,SHA,_directory=directory,_guard=g,_transport=transport)

def test_settle_thinking_and_cached_tokens_without_claiming_invoice(environment):
    directory,p,g=environment;t=Transport(directory,g)
    result=run(environment,t)
    assert result['usage']=={'inputTokens':100,'outputTokens':200,'cachedInputTokens':50}
    assert result['reservationCeilingUsd']=='0.0034'
    assert result['pricingStatus']=='CONSERVATIVE_CEILING_NOT_INVOICE'
    run(environment,t,'gemini','2'*32)
    rows=e.read_json(directory/'kimaru-r0-ledger.json')['entries']
    assert all(row['state']=='SETTLED' for row in rows)
    assert rows[-1]['usage']['outputTokens']==200
    assert not any('Synthetic' in json.dumps(row) for row in rows)
    with pytest.raises(e.EvaluationError,match='ALREADY_RECORDED'):run(environment,t)
    assert len(t.calls)==4

@pytest.mark.parametrize('limit',[0,0.001])
def test_zero_or_insufficient_budget_never_touches_credentials_or_transport(environment,limit):
    directory,p,g=environment;g.monthly_limit_usd=limit;t=Transport(directory,g)
    with pytest.raises((e.EvaluationError,BudgetExceededError)):run(environment,t)
    assert t.calls==[] and e.read_json(directory/'kimaru-r0-ledger.json')['entries']==[]

@pytest.mark.parametrize('change',[{'ownerApproved':False},{'geminiPaidTierConfirmed':False},{'syntheticOnly':False},{'candidate':'b'*40},{'limitUsd':'NaN'},{'expiresAt':'2000-01-01T00:00:00+00:00'}])
def test_policy_must_be_approved_current_paid_and_candidate_bound(environment,change):
    directory,p,g=environment;write_private(directory/'kimaru-r0-policy.json',{**p,**change});t=Transport(directory,g)
    with pytest.raises(e.EvaluationError):run(environment,t)
    assert t.calls==[]

def test_malformed_or_deleted_ledger_never_resets_budget(environment):
    directory,p,g=environment;t=Transport(directory,g);file=directory/'kimaru-r0-ledger.json'
    for content in ('{}','{broken','{"contract":"x","contract":"y"}'):
        file.write_text(content)
        with pytest.raises(e.EvaluationError):run(environment,t)
    file.unlink()
    with pytest.raises(e.EvaluationError):run(environment,t)
    assert t.calls==[]

def test_symlink_or_shared_policy_and_ledger_are_refused(environment):
    directory,p,g=environment;t=Transport(directory,g)
    for name in ('kimaru-r0-policy.json','kimaru-r0-ledger.json'):
        file=directory/name;original=file.read_bytes();file.chmod(0o644)
        with pytest.raises(e.EvaluationError):run(environment,t)
        file.chmod(0o600);file.unlink();target=directory/(name+'.saved');target.write_bytes(original);target.chmod(0o600);file.symlink_to(target)
        with pytest.raises(e.EvaluationError):run(environment,t)
        file.unlink();file.write_bytes(original);file.chmod(0o600)
    assert t.calls==[]

@pytest.mark.parametrize('result',[{}, {'model':'other','usage':{'input_tokens':100,'output_tokens':200}}, {'model':'gpt-6-sol','usage':{'input_tokens':100,'output_tokens':20000}}])
def test_missing_or_over_envelope_usage_preserves_reservations_and_freezes_batch(environment,result):
    directory,p,g=environment;t=Transport(directory,g,result=result)
    with pytest.raises(e.EvaluationError):run(environment,t)
    assert g._load_usage()['entries'][0]['status']=='hold'
    with pytest.raises(e.EvaluationError,match='UNKNOWN_OUTCOME'):run(environment,t,request='2'*32,case='retail-diagnosis')
    assert len(t.calls)==2

def test_unknown_response_survives_process_restart_age_and_month_change(environment):
    directory,p,g=environment;t=Transport(directory,g,error=TimeoutError('Never expose provider body'))
    with pytest.raises(e.EvaluationError,match='FAILED_OR_UNKNOWN'):run(environment,t)
    file=g._get_usage_file_path();data=json.loads(file.read_text());data['entries'][0]['timestamp']='2000-01-01T00:00:00+00:00';write_private(file,data)
    # Even a different ordinary operation must not expire this durable reservation.
    g._update_usage(lambda data:None)
    assert g._load_usage()['entries'][0]['status']=='hold'
    new=CostGuard(config_path=str(g.config_path),data_dir=str(directory/'new-month'),strict_ledger=True)
    t2=Transport(directory,new)
    with pytest.raises(e.EvaluationError,match='UNKNOWN_OUTCOME'):run((directory,p,new),t2,request='2'*32)
    assert t2.calls==[]

def test_batch_and_case_limits_survive_restarts_and_no_caller_limits_are_used(environment):
    directory,p,g=environment;p['limitUsd']='0.01';write_private(directory/'kimaru-r0-policy.json',p);write_private(directory/'kimaru-r0-ledger.json',dict(contract=e.CONTRACT,policyHash=e.digest(p),entries=[]));t=Transport(directory,g)
    with pytest.raises(e.EvaluationError,match='BATCH_BUDGET'):run(environment,t)
    assert t.calls==[]

@pytest.mark.parametrize('provider,change',[('azure',{'store':True}),('azure',{'tools':[]}),('azure',{'model':'gpt-6-astra'}),('azure',{'max_output_tokens':True}),('gemini',{'tools':[]})])
def test_caller_cannot_select_model_tools_storage_credentials_or_url(environment,provider,change):
    directory,p,g=environment;t=Transport(directory,g)
    with pytest.raises(e.EvaluationError):run(environment,t,provider,body={**payload(provider),**change})
    assert t.calls==[]

def test_request_arguments_and_direct_service_boundary(environment,monkeypatch):
    with pytest.raises(broker.BrokerError):broker.dispatch('kimaru_evaluation_generate',dict(provider='azure',payload={},request_id='1'*32,case_id='office-guideline',candidate_sha=SHA,url='https://other.invalid'))
    monkeypatch.setattr(broker,'IN_SERVICE',False)
    with pytest.raises((e.EvaluationError,broker.BrokerError)):run(environment,Transport(environment[0],environment[2]))

@pytest.mark.parametrize('contents',['{bad','{"entries":[{"cost_usd":-1}]}','{"entries":[{"cost_usd":NaN}]}'])
def test_strict_shared_ledger_is_fail_closed_but_legacy_behavior_is_unchanged(environment,contents):
    directory,p,g=environment;file=g._get_usage_file_path();file.write_text(contents);file.chmod(0o600)
    with pytest.raises(ValueError):g._load_usage()
    with pytest.raises(ValueError):g._update_usage(lambda x:None)
    assert file.read_text()==contents


def test_crash_during_local_settlement_never_releases_unknown_batch(environment):
    directory,p,g=environment;t=Transport(directory,g)
    with patch.object(e.Ledger,'settle',side_effect=OSError('synthetic disk error')):
        with pytest.raises(e.EvaluationError):run(environment,t)
    assert g._load_usage()['entries'][0]['status']=='settled'
    with pytest.raises(e.EvaluationError,match='UNKNOWN_OUTCOME'):run(environment,t,request='2'*32)
    assert len(t.calls)==2

@pytest.mark.parametrize('count,inputs,outputs',[ (40,5000,100),(15,5000,12000),(6,125000,100)])
def test_persisted_per_case_call_output_and_total_envelopes(environment,count,inputs,outputs):
    directory,p,g=environment;t=Transport(directory,g)
    rows=[]
    for n in range(count):
        rows.append(dict(requestId=f'{n+10:032x}',caseId='office-guideline',provider='azure',payloadHash='b'*64,inputLimit=inputs,outputLimit=outputs,holdId='h_'+f'{n:012x}',state='SETTLED',upperBoundUsd=str(e.cost(100,100)),usage={'inputTokens':100,'outputTokens':100,'cachedInputTokens':0}))
    write_private(directory/'kimaru-r0-ledger.json',dict(contract=e.CONTRACT,policyHash=e.digest(p),entries=rows))
    body=payload();body['input'][0]['content'][0]['text']='Synthetic ' *10000 if inputs>100000 else 'Synthetic'
    with pytest.raises(e.EvaluationError,match='CASE_ENVELOPE'):run(environment,t,body=body)
    assert t.calls==[]

def test_preflight_credential_or_region_failure_never_sends_or_reserves(environment):
    directory,p,g=environment;t=Transport(directory,g)
    with patch.object(t,'prepare',side_effect=e.EvaluationError('R0_AZURE_DEPLOYMENT_MISMATCH')):
        with pytest.raises(e.EvaluationError):run(environment,t)
    assert t.calls==[] and e.read_json(directory/'kimaru-r0-ledger.json')['entries']==[]
    assert g._load_usage()['entries']==[]


def test_fixed_azure_arm_identity_model_version_and_sku_are_checked_without_post():
    from unittest.mock import Mock
    account=dict(id=e.AZURE_ID,location='eastus',kind='OpenAI',properties=dict(endpoint=e.AZURE_BASE+'/',provisioningState='Succeeded'))
    deployment=dict(properties=dict(model={'format':'OpenAI','name':'gpt-6-sol','version':'2026-09-22'},provisioningState='Succeeded'),sku=dict(name='GlobalStandard',capacity=1000))
    for field in ('valid','region','endpoint','sku','version'):
        a,d=copy.deepcopy(account),copy.deepcopy(deployment)
        if field=='region':a['location']='japaneast'
        elif field=='endpoint':a['properties']['endpoint']='https://attacker.invalid'
        elif field=='sku':d['sku']['name']='Standard'
        elif field=='version':d['properties']['model']['version']='other'
        t=e.Transport();t.token=Mock(return_value='SYNTHETIC-TOKEN');t.request=Mock(side_effect=[a,d])
        if field=='valid':t.prepare('azure');assert t.credentials['azure']['Authorization']=='Bearer SYNTHETIC-TOKEN'
        else:
            with pytest.raises(e.EvaluationError):t.prepare('azure')
            assert t.credentials=={}
        assert all(call.args[0]=='GET' for call in t.request.call_args_list)
        assert all(call.args[1].startswith('https://management.azure.com'+e.AZURE_ID) for call in t.request.call_args_list)


def test_real_unix_socket_dispatch_reserves_before_fake_provider_and_redacts(environment):
    import socket,threading,tempfile
    from banto.broker_client import BrokerClient
    directory,p,g=environment;t=Transport(directory,g)
    original=e.generate
    with tempfile.TemporaryDirectory(dir='/tmp',prefix='r0-banto-') as tmp:
        path=Path(tmp)/'broker.sock';server=broker.Server(str(path),broker.Handler);path.chmod(0o600)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(e,'generate',side_effect=lambda **kw:original(**kw,_directory=directory,_guard=g,_transport=t)):
                result=BrokerClient(path).call('kimaru_evaluation_generate',provider='azure',payload=payload(),request_id='1'*32,case_id='office-guideline',candidate_sha=SHA)
                assert result['usage']['outputTokens']==200
                with pytest.raises(broker.BrokerError,match='ALREADY_RECORDED'):
                    BrokerClient(path).call('kimaru_evaluation_generate',provider='azure',payload=payload(),request_id='1'*32,case_id='office-guideline',candidate_sha=SHA)
                assert len(t.calls)==2
        finally:
            server.shutdown();server.server_close();thread.join()


def test_owner_policy_preparation_never_raises_a_zero_budget_or_overwrites_existing(environment):
    directory,p,g=environment
    expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    g.monthly_limit_usd=0
    with pytest.raises(e.EvaluationError,match='SHARED_BUDGET'):
        e.prepare(SHA,'50.00',expiry,True,True,_directory=directory,_guard=g)
    assert e.read_json(directory/'kimaru-r0-policy.json')==p
    g.monthly_limit_usd=50
    with pytest.raises(e.EvaluationError,match='ALREADY_EXISTS'):
        e.prepare(SHA,'50.00',expiry,True,True,_directory=directory,_guard=g)
    for name in ('kimaru-r0-policy.json','kimaru-r0-ledger.json'):(directory/name).unlink()
    result=e.prepare(SHA,'50.00',expiry,True,True,_directory=directory,_guard=g)
    assert result['realModelCalls']==0
    assert e.read_json(directory/'kimaru-r0-ledger.json')['entries']==[]
    assert (directory/'kimaru-r0-policy.json').stat().st_mode&0o077==0
    assert e.policy(directory)['candidate']==SHA
    for args in [('51.00',expiry,True,True),('50.00',expiry,False,True),('50.00',expiry,True,False),('NaN',expiry,True,True)]:
        with pytest.raises(e.EvaluationError):e.prepare(SHA,*args,_directory=directory,_guard=g)

@pytest.mark.parametrize('change,expected',[
    ('none',None),('metadata','FIXED_REGISTRATION'),('missing','REGISTRATION_REQUIRED'),
    ('login','GOOGLE_LOGIN'),('project','PROJECT_MISMATCH'),('billing','BILLING_REQUIRED'),('model','MODEL_UNAVAILABLE')])
def test_existing_gemini_preflight_has_fixed_project_billing_model_and_no_secret_returns(environment,monkeypatch,change,expected):
    from types import SimpleNamespace
    calls=[];key='AIza'+'S'*35
    entry=SimpleNamespace(account=e.GEMINI_ACCOUNT if change!='metadata' else 'unrelated',env_name='GEMINI_API_KEY')
    monkeypatch.setattr('banto.sync.config.SyncConfig.load',lambda:SimpleNamespace(keychain_service='synthetic-service',secrets={'gemini-api-key':entry}))
    class Store:
        def __init__(self,service_prefix):assert service_prefix=='synthetic-service'
        def get(self,account):
            calls.append(('key',account));assert account==e.GEMINI_ACCOUNT
            return None if change=='missing' else key
    monkeypatch.setattr('banto.keychain.KeychainStore',Store)
    monkeypatch.setattr('shutil.which',lambda _: '/synthetic/gcloud')
    def token(args,**kwargs):
        assert key not in str(args) and key not in str(kwargs)
        assert args==['/synthetic/gcloud','auth','print-access-token','--account','allnew.work2018@gmail.com','--quiet']
        return SimpleNamespace(returncode=1 if change=='login' else 0,stdout='synthetic-token')
    monkeypatch.setattr(e.subprocess,'run',token)
    def request(self,method,url,headers,payload=None):
        assert method=='GET' and payload is None
        calls.append(('get',url.split('?')[0]))
        if 'lookupKey?' in url:return {'parent':'projects/'+('000' if change=='project' else e.GEMINI_PROJECT_NUMBER)+'/locations/global'}
        if 'billingInfo' in url:return {'projectId':e.GEMINI_PROJECT,'billingEnabled':change!='billing'}
        return {'name':'models/'+e.GEMINI_MODEL,'supportedGenerationMethods':[] if change=='model' else ['generateContent']}
    monkeypatch.setattr(e.Transport,'request',request)
    if expected:
        with pytest.raises(e.EvaluationError,match=expected):e.preflight()
    else:
        result=e.preflight();assert result['keyValid'] and result['billingEnabled'] and result['realModelCalls']==0
        assert key not in json.dumps(result) and 'synthetic-token' not in json.dumps(result)
        assert len(calls)==4
    if change=='metadata':assert calls==[]
    if change=='project':assert len(calls)==2
    if change=='billing':assert len(calls)==3

def test_diagnosis_real_worker_output_limit_is_supported_without_widening_envelope():
    body=payload();body['max_output_tokens']=12288
    assert e.envelope('azure',body)[1]==12288
    body['max_output_tokens']=12289
    with pytest.raises(e.EvaluationError):e.envelope('azure',body)

@pytest.mark.parametrize('change',['valid','route','kind','cognitive','source','publisher','model'])
def test_foundry_actual_arm_metadata_preserves_exact_resource_endpoint_and_model(environment,change):
    account={'id':e.AZURE_ID,'location':'eastus','kind':'AIServices','properties':{'provisioningState':'Succeeded',
        'endpoint':'https://allnew-hontonotoko-ai.cognitiveservices.azure.com/',
        'endpoints':{'OpenAI Language Model Instance API':e.AZURE_BASE+'/'}}}
    deployment={'properties':{'provisioningState':'Succeeded','model':{'format':'OpenAI','name':'gpt-6-sol','version':'2026-09-22','publisher':None,'source':None,'sourceAccount':None,'callRateLimit':None}},'sku':{'name':'GlobalStandard','capacity':1000,'family':None}}
    if change=='route':account['properties']['endpoints']['OpenAI Language Model Instance API']='https://attacker.invalid'
    if change=='kind':account['kind']='Partner'
    if change=='cognitive':account['properties']['endpoint']='https://other.cognitiveservices.azure.com/'
    if change=='source':deployment['properties']['model']['source']='another-model'
    if change=='publisher':deployment['properties']['model']['publisher']='partner'
    if change=='model':deployment['properties']['model']['name']='gpt-6-astra'
    with patch.object(e.Transport,'token',return_value='SYNTHETIC'),patch.object(e.Transport,'request',side_effect=[account,deployment]) as req:
        if change=='valid':
            result=e.azure_preflight();assert result['realModelCalls']==0 and result['endpoint']==e.AZURE_BASE
            assert 'SYNTHETIC' not in json.dumps(result)
        else:
            with pytest.raises(e.EvaluationError):e.azure_preflight()
        assert all(call.args[0]=='GET' for call in req.call_args_list)
