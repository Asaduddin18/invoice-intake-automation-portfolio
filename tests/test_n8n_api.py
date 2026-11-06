"""Exercise the machine API against the real intake/report services and fake providers."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.n8n_routes import register,PREFIX
from app.main import create_app,Settings
from app.mail_intake import MailIntake
from app.monthly_report import MonthlyReport,reporting_period
from app.review import ReviewStore
from app.google_connection import GoogleError
from app.mail_store import MailBusyError
from scripts.prepare_n8n_api import prepare
from test_bills import FakeConnection
from test_google import google,auth,cache,MailGoogle,message,part,MAIL
from test_review import ROOT,TENANT,invoice

TOKEN='n8n-test-token-with-at-least-32-characters'
HEADERS={'Authorization':'Bearer '+TOKEN}


def make(tmp_path,monkeypatch,messages=()):
    monkeypatch.setenv('INVOICE_N8N_API_TOKEN',TOKEN)
    auth(tmp_path);connection=FakeConnection(tmp_path);g=google(tmp_path)
    provider=MailGoogle(list(messages));g.request=provider.request
    app=FastAPI();app.state.google=g
    app.state.mail_intake=MailIntake(connection,g,asyncio.Lock())
    app.state.monthly_report=MonthlyReport(connection,g)
    register(app,connection,SimpleNamespace(storage_dir=str(tmp_path),client_id='client',client_secret='secret'))
    return TestClient(app),app,connection,provider


@pytest.mark.parametrize('headers',[{}, {'Authorization':'Bearer wrong'},{'Authorization':'Basic '+TOKEN},
    {'Authorization':'Bearer '+TOKEN+'x'},{'Cookie':'INVOICE_N8N_API_TOKEN='+TOKEN}])
def test_every_endpoint_requires_dedicated_bearer(tmp_path,monkeypatch,headers):
    client,app,c,g=make(tmp_path,monkeypatch)
    for method,path,body in [('GET','status',None),('POST','intake',{}),('POST','report',{'month':'2026-08'})]:
        response=client.request(method,PREFIX+'/'+path,headers=headers,json=body)
        assert response.status_code==401
        assert response.json()['code']=='UNAUTHORIZED'
        assert len(response.json()['correlation_id'])==32
        assert TOKEN not in response.text
    assert not g.calls and not c.calls


def test_api_disabled_without_valid_server_token_and_browser_protections_unchanged(tmp_path,monkeypatch):
    monkeypatch.setenv('INVOICE_N8N_API_TOKEN','')
    client=TestClient(create_app(Settings('x','y',storage_dir=str(tmp_path))))
    assert client.get(PREFIX+'/status',headers=HEADERS).status_code==503
    assert client.post('/mail/poll',headers={'Origin':'http://evil.example'}).status_code==409
    assert client.post('/review/policy',headers={'Origin':'http://evil.example'}).status_code==403
    assert client.get('/health').status_code==200


def test_browser_origin_rejected_even_with_valid_token(tmp_path,monkeypatch):
    client,app,c,g=make(tmp_path,monkeypatch)
    for extra in ({'Origin':'http://localhost:8080'},{'Sec-Fetch-Site':'cross-site'}):
        response=client.post(PREFIX+'/intake',json={},headers={**HEADERS,**extra})
        assert response.status_code==403 and not g.calls and not c.calls


def test_status_local_readiness_no_provider_or_private_fields(tmp_path,monkeypatch):
    client,app,c,g=make(tmp_path,monkeypatch)
    response=client.get(PREFIX+'/status',headers=HEADERS)
    assert response.status_code==200
    assert response.json()['data']['readiness']=={'xero_saved':True,'google_saved':True,'xero_configured':True,'google_configured':True,'remote_access_checked':False}
    for secret in (MAIL,TOKEN,'access_token','refresh_token','supplier','filename','spreadsheet_id','tenant_id'):
        assert secret not in response.text
    assert not g.calls and not c.calls
    # A missing Google store remains visible through local readiness, without reconnecting.
    app.state.google.store.path.unlink()
    assert client.get(PREFIX+'/status',headers=HEADERS).json()['data']['readiness']['google_saved'] is False
    assert client.post(PREFIX+'/intake',headers=HEADERS,json={}).json()['code']=='CONNECTION_REQUIRED'
    assert not g.calls


@pytest.mark.parametrize('payload',[{'max_new_extractions':3},{'automatic':True},[],None])
def test_intake_cannot_override_zero_cost_boundary(tmp_path,monkeypatch,payload):
    client,app,c,g=make(tmp_path,monkeypatch)
    response=client.post(PREFIX+'/intake',headers={**HEADERS,'Content-Type':'application/json'},content=json.dumps(payload))
    assert response.status_code==400 and not g.calls


def test_oversized_malformed_and_non_json_bodies_have_safe_errors(tmp_path,monkeypatch):
    client,app,c,g=make(tmp_path,monkeypatch)
    for raw,ctype in [('secret-upstream: not JSON','application/json'),('x'*1025,'application/json'),('{}','text/plain')]:
        response=client.post(PREFIX+'/intake',headers={**HEADERS,'Content-Type':ctype},content=raw)
        assert response.status_code==400 and response.json()['code']=='INVALID_REQUEST'
        assert 'secret-upstream' not in response.text and 'x'*1025 not in response.text
    assert not g.calls


def test_uncached_pdf_never_invokes_model_with_key_present(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','configured-but-forbidden')
    blob=(ROOT/'fixtures/invoice_001.pdf').read_bytes()
    client,app,c,g=make(tmp_path,monkeypatch,[message('uncached',[part(blob)])])
    async def forbid(*args,**kwargs):raise AssertionError('Paid extraction forbidden')
    app.state.mail_intake.runs.run=forbid
    result=client.post(PREFIX+'/intake',headers=HEADERS,json={})
    assert result.status_code==200
    assert result.json()['data']['counts']=={'needs_inspection':1}
    assert result.json()['data']['new_extraction_budget']==0 and not c.calls
    assert client.post(PREFIX+'/intake',headers=HEADERS,json={}).json()['data']['poll']['new_deliveries']==0


def test_cached_valid_duplicate_and_report_retry_reuse_real_services(tmp_path,monkeypatch):
    cache(tmp_path);blob=(ROOT/'fixtures/invoice_001.pdf').read_bytes()
    client,app,c,g=make(tmp_path,monkeypatch,[message('first',[part(blob)]),message('second',[part(blob,'renamed.pdf')])])
    result=client.post(PREFIX+'/intake',headers=HEADERS,json={}).json()
    assert result['data']['counts']=={'verified':2}
    assert result['data']['duplicate_deliveries']==1
    assert result['data']['available_report_months']==['2026-08']
    assert len([call for call in c.calls if call[:2]==('POST','/Invoices')])==1
    second=client.post(PREFIX+'/intake',headers=HEADERS,json={}).json()
    assert second['data']['poll']=={'new_deliveries':0,'already_recorded':2,'more_pages':False}
    prior=list(c.calls)
    g.fail_report=True
    failure=client.post(PREFIX+'/report',headers=HEADERS,json={'month':'2026-08'})
    assert failure.status_code==503 and 'simulated' not in failure.text
    assert failure.json()['data']['automatic_retry'] is False
    g.fail_report=False
    report=client.post(PREFIX+'/report',headers=HEADERS,json={'month':'2026-08'})
    assert report.status_code==200 and report.json()['code']=='REPORT_VERIFIED'
    assert 'spreadsheet_id' not in report.text and c.calls==prior
    assert g.creates==1 and report.json()['data']['deliveries']==2
    counts=client.get(PREFIX+'/status',headers=HEADERS).json()['data']
    assert counts['reports']==[{'month':'2026-08','status':'verified'}]
    for private in (MAIL,'AP-2026-1001','renamed.pdf','bill-id','sheet-1'):
        assert private not in json.dumps([result,second,report.json(),counts])


@pytest.mark.parametrize('mode,invalid,expected',[('required',False,'awaiting_approval'),('automatic',True,'held')])
def test_api_preserves_review_policy_and_invalid_holds(tmp_path,monkeypatch,mode,invalid,expected):
    candidate=invoice()
    if invalid:candidate['invoice_number']=None
    cache(tmp_path,candidate=candidate)
    ReviewStore(tmp_path).set_policy(TENANT,mode,'Test current operator policy')
    client,app,c,g=make(tmp_path,monkeypatch,[message('hold',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())])])
    result=client.post(PREFIX+'/intake',headers=HEADERS,json={})
    assert result.status_code==200 and result.json()['data']['counts']=={expected:1}
    assert not c.calls


@pytest.mark.parametrize('value',['2026-13','2026-8','secret-month',3,None])
def test_report_rejects_invalid_or_unknown_month_without_provider_calls(tmp_path,monkeypatch,value):
    client,app,c,g=make(tmp_path,monkeypatch)
    response=client.post(PREFIX+'/report',headers=HEADERS,json={'month':value})
    assert response.status_code==400 and not g.calls and not c.calls
    assert client.post(PREFIX+'/report',headers=HEADERS,json={'month':'2026-08'}).json()['code']=='UNKNOWN_REPORT_MONTH'


def test_busy_and_provider_errors_have_fixed_codes_no_sensitive_text(tmp_path,monkeypatch):
    client,app,c,g=make(tmp_path,monkeypatch)
    async def failure(**kwargs):raise GoogleError('private invoice AP-123 and provider secret')
    app.state.mail_intake.poll=failure
    response=client.post(PREFIX+'/intake',headers=HEADERS,json={})
    assert response.status_code==503 and 'private' not in response.text
    async def busy(**kwargs):raise MailBusyError('private lock path')
    app.state.mail_intake.poll=busy
    response=client.post(PREFIX+'/intake',headers=HEADERS,json={})
    assert response.status_code==409 and response.json()['code']=='OPERATION_BUSY'


def test_period_rule_preserves_invoice_date_and_receipt_fallback():
    assert reporting_period('2026-08-13',1793624400)==('2026-08','invoice date')
    for bad in (None,'invalid','2026-02-30'):
        assert reporting_period(bad,1793624400)[0]=='2026-11'


def test_api_token_setup_preserves_existing_env_and_secret(tmp_path):
    env=tmp_path/'.env';env.write_text('GOOGLE_CLIENT_ID=keep-me\n')
    first=prepare(tmp_path).read_text()
    assert first.startswith('Bearer ') and len(first)==71
    assert prepare(tmp_path).read_text()==first
    assert 'GOOGLE_CLIENT_ID=keep-me' in env.read_text()
    assert env.stat().st_mode & 0o777==0o600
    assert (tmp_path/'.runtime/n8n/api-token.txt').stat().st_mode & 0o777==0o600


@pytest.mark.parametrize('budget',[-1,4,True,0.5])
def test_invalid_service_budget_rejected_before_provider_access(tmp_path,monkeypatch,budget):
    client,app,c,g=make(tmp_path,monkeypatch)
    with pytest.raises(ValueError):asyncio.run(app.state.mail_intake.poll(max_new_extractions=budget))
    assert not g.calls and not c.calls


def test_existing_intake_lock_returns_busy_without_provider_access(tmp_path,monkeypatch):
    client,app,c,g=make(tmp_path,monkeypatch)
    with app.state.mail_intake.store.gate('intake'):
        response=client.post(PREFIX+'/intake',headers=HEADERS,json={})
    assert response.status_code==409 and response.json()['code']=='OPERATION_BUSY'
    assert not g.calls and not c.calls
