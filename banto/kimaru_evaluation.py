"""Budgeted, synthetic-only Kimaru R0 generation in the common broker.

No caller credentials/URL/tools, policy writes, retry or unknown-hold expiry.
Policy and initial ledger must be explicitly prepared by the owner. A zero
shared budget stops before credential access or network. Prices below are
reservation ceilings, not deployed-contract invoices or measured costs.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import urllib.request
import urllib.error

CONTRACT = 'kimaru-r0-v1'
CASES = tuple(c + '-' + p for c in ('office','retail','travel') for p in ('guideline','diagnosis'))
SUBSCRIPTION = '49805a83-6ad1-489f-886d-891dbf12a2fc'
AZURE_BASE = 'https://allnew-hontonotoko-ai.openai.azure.com'
AZURE_ID = '/subscriptions/'+SUBSCRIPTION+'/resourceGroups/rg-hontonotoko-ai/providers/Microsoft.CognitiveServices/accounts/allnew-hontonotoko-ai'
GEMINI_PROJECT = 'gen-lang-client-0469915824'
GEMINI_PROJECT_NUMBER = '402783811468'
GEMINI_ACCOUNT = 'claude-mcp-gemini'
MAX_OUTPUT = 12288
GEMINI_MODEL = 'gemini-3.8-flash'
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/'+GEMINI_MODEL+':generateContent'
PROVIDERS = {'azure': ('gpt-6-sol', 'azure_openai'), 'gemini': (GEMINI_MODEL, 'google')}
RATES = {'inputPerMillionUsd': '4', 'outputPerMillionUsd': '15'}

from .broker_client import BrokerError
class EvaluationError(BrokerError): pass

def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def cost(i,o): return Decimal(i)*Decimal(4)/1000000 + Decimal(o)*Decimal(15)/1000000

def private_file(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>500000:
        raise EvaluationError('R0_PRIVATE_FILE_INVALID')

def read_json(path):
    private_file(path)
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise EvaluationError('R0_DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as f: return json.load(f,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError('R0_JSON_INVALID')))

def policy(directory):
    p=read_json(directory/'kimaru-r0-policy.json')
    fields={'contract','candidate','batchId','limitUsd','expiresAt','ownerApproved','syntheticOnly','geminiPaidTierConfirmed','rates'}
    if (not isinstance(p,dict) or set(p)!=fields or p['contract']!=CONTRACT
        or not re.fullmatch('[a-f0-9]{40}',p['candidate']) or not re.fullmatch('[a-f0-9]{32}',p['batchId'])
        or any(p[k] is not True for k in ('ownerApproved','syntheticOnly','geminiPaidTierConfirmed')) or p['rates']!=RATES
        or not isinstance(p['limitUsd'],str) or not re.fullmatch(r'\d{1,3}(\.\d{1,2})?',p['limitUsd'])
        or not 0<Decimal(p['limitUsd'])<=Decimal('50')):
        raise EvaluationError('R0_APPROVED_POLICY_REQUIRED')
    expires=datetime.fromisoformat(p['expiresAt'])
    if expires.tzinfo is None or expires<=datetime.now(timezone.utc): raise EvaluationError('R0_POLICY_EXPIRED')
    return p

class Ledger:
    def __init__(self,directory,p): self.directory=directory;self.policy=p;self.path=directory/'kimaru-r0-ledger.json'
    @contextmanager
    def locked(self):
        lock=self.directory/'kimaru-r0-ledger.lock'
        fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077: raise EvaluationError('R0_LOCK_INVALID')
            fcntl.flock(fd,fcntl.LOCK_EX)
            self.data=read_json(self.path) # Missing/corrupt ledger never starts from zero.
            if (not isinstance(self.data,dict) or set(self.data)!={'contract','policyHash','entries'}
                or self.data['contract']!=CONTRACT or self.data['policyHash']!=digest(self.policy)
                or not isinstance(self.data['entries'],list) or len(self.data['entries'])>240):
                raise EvaluationError('R0_LEDGER_INVALID')
            ids=set()
            for row in self.data['entries']:
                fields={'requestId','caseId','provider','payloadHash','inputLimit','outputLimit','holdId','state','upperBoundUsd','usage'}
                if (not isinstance(row,dict) or set(row)!=fields or row['requestId'] in ids
                    or not re.fullmatch('[a-f0-9]{32}',row['requestId']) or row['caseId'] not in CASES or row['provider'] not in PROVIDERS
                    or not re.fullmatch('[a-f0-9]{64}',row['payloadHash']) or not re.fullmatch('h_[a-f0-9]{12}',row['holdId'])
                    or row['state'] not in ('RESERVED','SETTLED')
                    or any(type(row[k])!=int or row[k]<=0 for k in ('inputLimit','outputLimit'))
                    or row['outputLimit']>MAX_OUTPUT or row['inputLimit']>650000):
                    raise EvaluationError('R0_LEDGER_INVALID')
                ids.add(row['requestId'])
                maximum=cost(row['inputLimit'],row['outputLimit'])
                if row['state']=='RESERVED':
                    if row['usage'] is not None or row['upperBoundUsd']!=str(maximum): raise EvaluationError('R0_LEDGER_INVALID')
                else:
                    u=row['usage']
                    if (not isinstance(u,dict) or set(u)!={'inputTokens','outputTokens','cachedInputTokens'}
                        or any(type(v)!=int or v<0 for v in u.values()) or not 0<u['inputTokens']<=row['inputLimit']
                        or u['outputTokens']>row['outputLimit'] or u['cachedInputTokens']>u['inputTokens']
                        or row['upperBoundUsd']!=str(cost(u['inputTokens'],u['outputTokens']))): raise EvaluationError('R0_LEDGER_INVALID')
            yield self
        finally:
            os.close(fd)
    def save(self):
        fd,tmp=tempfile.mkstemp(prefix='r0-ledger-',dir=self.directory)
        try:
            with os.fdopen(fd,'wb') as f: f.write(canonical(self.data));f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
            directory_fd=os.open(self.directory,os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        finally:
            Path(tmp).unlink(missing_ok=True)
    def check(self,request_id,case_id,i,o):
        entries=self.data['entries']
        if any(row['requestId']==request_id for row in entries): raise EvaluationError('R0_REQUEST_ALREADY_RECORDED')
        if any(row['state']=='RESERVED' for row in entries): raise EvaluationError('R0_UNKNOWN_OUTCOME_REVIEW_REQUIRED')
        rows=[row for row in entries if row['caseId']==case_id]
        if len(rows)>=40 or sum(r['outputLimit'] for r in rows)+o>180000 or sum(r['inputLimit']+r['outputLimit'] for r in rows)+i+o>800000:
            raise EvaluationError('R0_CASE_ENVELOPE_EXCEEDED')
        if sum(Decimal(r['upperBoundUsd']) for r in entries)+cost(i,o)>Decimal(self.policy['limitUsd']): raise EvaluationError('R0_BATCH_BUDGET_EXCEEDED')
    def reserve(self,request_id,case_id,provider,payload,i,o,hold):
        row=dict(requestId=request_id,caseId=case_id,provider=provider,payloadHash=digest(payload),inputLimit=i,outputLimit=o,holdId=hold,state='RESERVED',upperBoundUsd=str(cost(i,o)),usage=None)
        self.data['entries'].append(row);self.save();return row
    def settle(self,row,u):
        row.update(state='SETTLED',usage=u,upperBoundUsd=str(cost(u['inputTokens'],u['outputTokens'])));self.save()

def envelope(provider,payload):
    if provider not in PROVIDERS or not isinstance(payload,dict): raise EvaluationError('R0_REQUEST_INVALID')
    if provider=='azure':
        fields={'model','store','instructions','input','reasoning','max_output_tokens','text'}
        if (set(payload)!=fields or payload['model']!='gpt-6-sol' or payload['store'] is not False
            or not isinstance(payload['instructions'],str) or payload['reasoning']!={'effort':'medium'}
            or not isinstance(payload['text'],dict) or set(payload['text'])!={'format'}
            or not isinstance(payload['text']['format'],dict) or set(payload['text']['format'])!={'type','name','strict','schema'}
            or payload['text']['format']['type']!='json_schema' or payload['text']['format']['strict'] is not True
            or not re.fullmatch('[a-z_]{1,64}',payload['text']['format']['name'])): raise EvaluationError('R0_AZURE_PAYLOAD_INVALID')
        items=payload['input'];o=payload['max_output_tokens']
        if not isinstance(items,list) or len(items)!=1 or items[0].get('role')!='user' or set(items[0])!={'role','content'}: raise EvaluationError('R0_TEXT_ONLY_REQUIRED')
        content=items[0]['content']
        if not isinstance(content,list) or len(content)!=1 or set(content[0])!={'type','text'} or content[0]['type']!='input_text' or not isinstance(content[0]['text'],str): raise EvaluationError('R0_TEXT_ONLY_REQUIRED')
    else:
        if set(payload)!={'systemInstruction','contents','generationConfig'}: raise EvaluationError('R0_GEMINI_PAYLOAD_INVALID')
        config=payload['generationConfig'];o=config.get('maxOutputTokens')
        if (set(config)!={'responseMimeType','responseSchema','maxOutputTokens','thinkingConfig'} or config['responseMimeType']!='application/json'
            or config['thinkingConfig']!={'thinkingLevel':'low'}): raise EvaluationError('R0_GEMINI_PAYLOAD_INVALID')
        for part in (payload['systemInstruction'], *payload['contents']):
            if not isinstance(part,dict) or set(part)-{'role','parts'} or ('role' in part and part['role']!='user') or not isinstance(part.get('parts'),list) or len(part['parts'])!=1 or set(part['parts'][0])!={'text'} or not isinstance(part['parts'][0]['text'],str): raise EvaluationError('R0_TEXT_ONLY_REQUIRED')
        if len(payload['contents'])!=1: raise EvaluationError('R0_TEXT_ONLY_REQUIRED')
    i=len(canonical(payload))+4096
    if type(o)!=int or not 1<=o<=MAX_OUTPUT or i>650000: raise EvaluationError('R0_TOKEN_LIMIT_INVALID')
    return i,o

def usage(provider,result,i,o):
    if not isinstance(result,dict): raise EvaluationError('R0_USAGE_INVALID')
    if provider=='azure':
        u=result.get('usage',{});a=u.get('input_tokens');b=u.get('output_tokens');cached=u.get('input_tokens_details',{}).get('cached_tokens',0)
        if result.get('model') not in ('gpt-6-sol','gpt-6-sol-2026-09-22'): raise EvaluationError('R0_MODEL_MISMATCH')
    else:
        u=result.get('usageMetadata',{});a=u.get('promptTokenCount');candidate=u.get('candidatesTokenCount',0);thought=u.get('thoughtsTokenCount',0);cached=u.get('cachedContentTokenCount',0)
        if any(type(v)!=int or v<0 for v in (candidate,thought)) or u.get('toolUsePromptTokenCount',0)!=0: raise EvaluationError('R0_USAGE_INVALID')
        b=candidate+thought
        if u.get('totalTokenCount')!=a+b or result.get('modelVersion')!=GEMINI_MODEL: raise EvaluationError('R0_USAGE_INVALID')
    if any(type(v)!=int for v in (a,b,cached)) or not 0<a<=i or not 0<=b<=o or not 0<=cached<=a: raise EvaluationError('R0_USAGE_INVALID')
    return dict(inputTokens=a,outputTokens=b,cachedInputTokens=cached)

class Transport:
    """Authentication and raw provider responses remain within the common service."""
    def __init__(self):
        from .kimaru_material import NoRedirect
        import ssl
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self.credentials={}
    def token(self,audience):
        from .broker import observe_secret
        import shutil
        az=shutil.which('az')
        if not az: raise EvaluationError('R0_OWNER_AZURE_LOGIN_REQUIRED')
        safe={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR','AZURE_CONFIG_DIR')}
        r=subprocess.run([az,'account','get-access-token','--subscription',SUBSCRIPTION,'--resource',audience,'--query','accessToken','-o','tsv'],capture_output=True,text=True,timeout=30,env=safe)
        token=r.stdout.strip()
        if r.returncode or not token or len(token)>16384: raise EvaluationError('R0_OWNER_AZURE_LOGIN_REQUIRED')
        observe_secret(token);return token
    def request(self,method,url,headers,payload=None):
        request=urllib.request.Request(url,data=canonical(payload) if payload is not None else None,headers={**headers,'Content-Type':'application/json'},method=method)
        try:
            with self.opener.open(request,timeout=120 if method=='POST' else 30) as response: raw=response.read(2000001)
            if len(raw)>2000000: raise EvaluationError('R0_RESPONSE_TOO_LARGE')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            error.close();raise EvaluationError('R0_REMOTE_FAILED_OR_UNKNOWN') from None
        except (OSError,ValueError): raise EvaluationError('R0_REMOTE_FAILED_OR_UNKNOWN') from None
    def prepare(self,provider):
        from .broker import observe_secret
        if provider=='azure':
            arm={'Authorization':'Bearer '+self.token('https://management.azure.com/')}
            account=self.request('GET','https://management.azure.com'+AZURE_ID+'?api-version=2025-06-01',arm)
            deployment=self.request('GET','https://management.azure.com'+AZURE_ID+'/deployments/gpt-6-sol?api-version=2025-06-01',arm)
            if (account.get('id','').lower()!=AZURE_ID.lower() or account.get('location')!='eastus' or account.get('kind') not in ('OpenAI','AIServices')
                or not azure_route(account)
                or account.get('properties',{}).get('provisioningState')!='Succeeded'
                or any(deployment.get('properties',{}).get('model',{}).get(k)!=v for k,v in {'format':'OpenAI','name':'gpt-6-sol','version':'2026-09-22'}.items())
                or any(deployment.get('properties',{}).get('model',{}).get(k) is not None for k in ('publisher','source','sourceAccount'))
                or deployment.get('properties',{}).get('provisioningState')!='Succeeded' or deployment.get('sku',{}).get('name')!='GlobalStandard'
                or type(deployment.get('sku',{}).get('capacity'))!=int or deployment['sku']['capacity']<=0): raise EvaluationError('R0_AZURE_DEPLOYMENT_MISMATCH')
            self.credentials[provider]={'Authorization':'Bearer '+self.token('https://ai.azure.com/')}
        else:
            self.gemini_preflight()
    def gemini_preflight(self):
        from .broker import observe_secret
        from .keychain import KeychainStore
        from .sync.config import SyncConfig
        import shutil
        from urllib.parse import urlencode
        config=SyncConfig.load()
        entry=config.secrets.get('gemini-api-key')
        if entry is None or entry.account!=GEMINI_ACCOUNT or entry.env_name!='GEMINI_API_KEY':
            raise EvaluationError('R0_GEMINI_FIXED_REGISTRATION_REQUIRED')
        value=KeychainStore(service_prefix=config.keychain_service).get(GEMINI_ACCOUNT)
        if not isinstance(value,str) or not re.fullmatch('AIza[A-Za-z0-9_-]{26,196}',value):
            raise EvaluationError('R0_GEMINI_REGISTRATION_REQUIRED')
        observe_secret(value)
        gcloud=shutil.which('gcloud')
        if not gcloud:raise EvaluationError('R0_OWNER_GOOGLE_LOGIN_REQUIRED')
        safe={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR')}
        result=subprocess.run([gcloud,'auth','print-access-token','--account','allnew.work2018@gmail.com','--quiet'],capture_output=True,text=True,timeout=30,env=safe)
        token=result.stdout.strip()
        if result.returncode or not token or len(token)>16384:raise EvaluationError('R0_OWNER_GOOGLE_LOGIN_REQUIRED')
        observe_secret(token);auth={'Authorization':'Bearer '+token}
        # The key stays inside this service, including the fixed Google lookup URL.
        # No argv/env/keyString/URL or provider body is returned to the caller/log.
        lookup=self.request('GET','https://apikeys.googleapis.com/v2/keys:lookupKey?'+urlencode({'keyString':value}),auth)
        if lookup.get('parent')!='projects/'+GEMINI_PROJECT_NUMBER+'/locations/global':
            raise EvaluationError('R0_GEMINI_PROJECT_MISMATCH')
        billing=self.request('GET','https://cloudbilling.googleapis.com/v1/projects/'+GEMINI_PROJECT+'/billingInfo',auth)
        if billing.get('projectId')!=GEMINI_PROJECT or billing.get('billingEnabled') is not True:
            raise EvaluationError('R0_GEMINI_BILLING_REQUIRED')
        headers={'x-goog-api-key':value}
        model=self.request('GET','https://generativelanguage.googleapis.com/v1beta/models/'+GEMINI_MODEL,headers)
        if model.get('name')!='models/'+GEMINI_MODEL or 'generateContent' not in model.get('supportedGenerationMethods',[]):
            raise EvaluationError('R0_GEMINI_MODEL_UNAVAILABLE')
        self.credentials['gemini']=headers
        return dict(projectId=GEMINI_PROJECT,projectNumber=GEMINI_PROJECT_NUMBER,billingEnabled=True,
                    model=GEMINI_MODEL,keyValid=True,secretReturned=False,realModelCalls=0)
    def send(self,provider,payload):
        return self.request('POST',AZURE_BASE+'/openai/v1/responses' if provider=='azure' else GEMINI_URL,self.credentials[provider],payload)

def prepare(candidate_sha,limit_usd,expires_at,owner_confirm,gemini_paid_tier_confirmed,*,_directory=None,_guard=None):
    from .broker import require_service,IN_SERVICE
    require_service()
    if not IN_SERVICE or owner_confirm is not True or gemini_paid_tier_confirmed is not True:
        raise EvaluationError('R0_OWNER_AND_PAID_TIER_CONFIRMATION_REQUIRED')
    if not isinstance(candidate_sha,str) or not re.fullmatch('[a-f0-9]{40}',candidate_sha) or not isinstance(limit_usd,str) or not re.fullmatch(r'\d{1,3}(\.\d{1,2})?',limit_usd) or not 0<Decimal(limit_usd)<=50:
        raise EvaluationError('R0_POLICY_ARGUMENTS_INVALID')
    try:
        expiry=datetime.fromisoformat(expires_at)
        now=datetime.now(timezone.utc)
        from datetime import timedelta
        if expiry.tzinfo is None or not now<expiry<=now+timedelta(days=7): raise ValueError()
        from .guard import CostGuard,CONFIG_DIR
        guard=_guard or CostGuard(caller='kimaru-r0',strict_ledger=True)
        directory=_directory or CONFIG_DIR
        if not guard.strict_ledger or guard.monthly_limit_usd<=0 or guard.get_remaining_budget()['remaining_usd']<float(limit_usd):
            raise EvaluationError('R0_SHARED_BUDGET_REQUIRED')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if directory.is_symlink() or directory.stat().st_uid!=os.getuid() or directory.stat().st_mode&0o022:
            raise EvaluationError('R0_POLICY_DIRECTORY_INVALID')
        import uuid
        p=dict(contract=CONTRACT,candidate=candidate_sha,batchId=uuid.uuid4().hex,limitUsd=limit_usd,expiresAt=expires_at,ownerApproved=True,syntheticOnly=True,geminiPaidTierConfirmed=True,rates=RATES)
        # Hold the same stable lock as generation. Refuse any existing policy/ledger.
        fd=os.open(directory/'kimaru-r0-ledger.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            if os.fstat(fd).st_mode&0o077 or os.fstat(fd).st_uid!=os.getuid(): raise EvaluationError('R0_LOCK_INVALID')
            fcntl.flock(fd,fcntl.LOCK_EX)
            files=[directory/'kimaru-r0-policy.json',directory/'kimaru-r0-ledger.json']
            if any(path.exists() or path.is_symlink() for path in files): raise EvaluationError('R0_POLICY_ALREADY_EXISTS_REVIEW_REQUIRED')
            for path,data in zip(files,[p,dict(contract=CONTRACT,policyHash=digest(p),entries=[])]):
                output=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(output,'wb') as f: f.write(canonical(data));f.flush();os.fsync(f.fileno())
            dfd=os.open(directory,os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        finally: os.close(fd)
        return dict(contract=CONTRACT,candidate=candidate_sha,batchId=p['batchId'],limitUsd=limit_usd,expiresAt=expires_at,realModelCalls=0)
    except EvaluationError: raise
    except Exception: raise EvaluationError('R0_POLICY_PREPARATION_FAILED_OR_PARTIAL') from None


def capabilities():
    return dict(contract=CONTRACT,syntheticOnly=True,providers=list(PROVIDERS),policyWrites=True,
        reservationRates=RATES,unknownOutcomeBlocksBatch=True,automaticRetries=False)

def generate(provider,payload,request_id,case_id,candidate_sha,*,_directory=None,_guard=None,_transport=None):
    from .broker import require_service,IN_SERVICE
    require_service()
    if not IN_SERVICE: raise EvaluationError('R0_COMMON_BROKER_REQUIRED')
    if (not isinstance(request_id,str) or not re.fullmatch('[a-f0-9]{32}',request_id) or case_id not in CASES
        or not isinstance(candidate_sha,str) or not re.fullmatch('[a-f0-9]{40}',candidate_sha)): raise EvaluationError('R0_CONTEXT_INVALID')
    try: i,o=envelope(provider,payload)
    except EvaluationError: raise
    except Exception: raise EvaluationError('R0_REQUEST_INVALID') from None
    from .guard import CostGuard,CONFIG_DIR
    directory=_directory or CONFIG_DIR
    try:
        p=policy(directory)
        if p['candidate']!=candidate_sha: raise EvaluationError('R0_CANDIDATE_MISMATCH')
        guard=_guard or CostGuard(caller='kimaru-r0',strict_ledger=True)
        if not guard.strict_ledger or guard.monthly_limit_usd<=0: raise EvaluationError('R0_SHARED_BUDGET_REQUIRED')
        model,provider_name=PROVIDERS[provider]
        guard.pricing[model]=dict(type='per_token',input_per_1k=0.004,output_per_1k=0.015)
        with Ledger(directory,p).locked() as ledger:
            ledger.check(request_id,case_id,i,o)
            guard.check_budget(model,provider=provider_name,input_tokens=i,output_tokens=o)
            transport=_transport or Transport()
            transport.prepare(provider) # No generation charge; no key access when budget is zero.
            if datetime.fromisoformat(p['expiresAt'])<=datetime.now(timezone.utc): raise EvaluationError('R0_POLICY_EXPIRED')
            hold=guard.hold_budget(model,provider=provider_name,input_tokens=i,output_tokens=o,durable=True)
            row=ledger.reserve(request_id,case_id,provider,payload,i,o,hold)
            result=transport.send(provider,payload) # The two durable writes complete before sending.
            u=usage(provider,result,i,o)
            guard.settle_hold(hold,model=model,provider=provider_name,input_tokens=u['inputTokens'],output_tokens=u['outputTokens'],operation='kimaru-r0')
            ledger.settle(row,u)
            return dict(contract=CONTRACT,requestId=request_id,caseId=case_id,provider=provider,candidate=candidate_sha,
                usage=u,reservationCeilingUsd=row['upperBoundUsd'],pricingStatus='CONSERVATIVE_CEILING_NOT_INVOICE',response=result)
    except EvaluationError: raise
    except Exception: raise EvaluationError('R0_FAILED_OR_UNKNOWN') from None


def preflight():
    from .broker import require_service,IN_SERVICE
    require_service()
    if not IN_SERVICE:raise EvaluationError('R0_COMMON_BROKER_REQUIRED')
    try:return Transport().gemini_preflight()
    except EvaluationError:raise
    except Exception:raise EvaluationError('R0_PREFLIGHT_FAILED') from None


def azure_route(account):
    properties=account.get('properties',{})
    if account.get('kind')=='OpenAI':return properties.get('endpoint','').rstrip('/')==AZURE_BASE
    return (account.get('kind')=='AIServices'
        and properties.get('endpoint','').rstrip('/')=='https://allnew-hontonotoko-ai.cognitiveservices.azure.com'
        and properties.get('endpoints',{}).get('OpenAI Language Model Instance API','').rstrip('/')==AZURE_BASE)


def azure_preflight():
    from .broker import require_service,IN_SERVICE
    require_service()
    if not IN_SERVICE:raise EvaluationError('R0_COMMON_BROKER_REQUIRED')
    try:
        Transport().prepare('azure')
        return dict(resourceId=AZURE_ID,endpoint=AZURE_BASE,region='eastus',model='gpt-6-sol',version='2026-09-22',sku='GlobalStandard',secretReturned=False,realModelCalls=0)
    except EvaluationError:raise
    except Exception:raise EvaluationError('R0_AZURE_PREFLIGHT_FAILED') from None
