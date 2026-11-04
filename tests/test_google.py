import asyncio
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from app.connection import TokenStore
from app.google_connection import GoogleConnection,GoogleError,GoogleStore,GMAIL,SHEETS,SCOPES
from app.mail_store import MailStore
from app.mail_intake import MailIntake,attachments,decode_body
from app.monthly_report import MonthlyReport
from app.extraction import PROMPT
from app.extraction_runs import ExtractionRuns
from app.review import ReviewStore
from app.main import Settings,create_app
from test_bills import FakeConnection
from test_review import seed,invoice,TENANT,ROOT

MAIL='intake@example.com'

def google(tmp_path,transport=None):
    return GoogleConnection(tmp_path,client_id='client',client_secret='secret',mailbox=MAIL,transport=transport)

def auth(tmp_path):
    GoogleStore(tmp_path).save({'access_token':'access','refresh_token':'refresh','expires_at':time.time()+3600,'mailbox':MAIL})


def cache(tmp_path,issues=None,candidate=None,source=None):
    case=seed(tmp_path,candidate,issues);runs=ExtractionRuns(tmp_path);r=runs.load(case['id'])
    r.pop('fixture_note',None)
    r.update(prompt_sha256=hashlib.sha256(PROMPT.read_bytes()).hexdigest(),requested_model='deepseek-flash')
    if source is not None:
        (runs.folder(r['id'])/'source.pdf').write_bytes(source);r['source_sha256']=hashlib.sha256(source).hexdigest()
        # Test sources generally use original bytes; case changes below require no active bill.
    runs.save(r)
    return r


def part(data,name='invoice.pdf',pid='1'):
    return {'partId':pid,'filename':name,'mimeType':'application/pdf','body':{'data':base64.urlsafe_b64encode(data).decode(),'size':len(data)}}

class MailGoogle:
    mailbox=MAIL
    def __init__(self,messages):self.messages=messages;self.calls=[];self.fail_report=False;self.sheet=[];self.creates=0
    async def request(self,method,url,**kw):
        self.calls.append((method,url,kw))
        if url==GMAIL+'/profile':return {'emailAddress':MAIL}
        if url==GMAIL+'/labels':return {'labels':[{'id':'label-1','name':'Invoices-Demo'}]}
        if url==GMAIL+'/messages':return {'messages':[{'id':m['id']} for m in self.messages]}
        if url.startswith(GMAIL+'/messages/'):
            return next(m for m in self.messages if m['id']==url.rsplit('/',1)[1])
        if url==SHEETS:
            self.creates+=1;return {'spreadsheetId':'sheet-1'}
        if url==SHEETS+'/sheet-1' or url==SHEETS+'/existing-sheet-id':
            return {'sheets':[{'properties':{'title':'Intake','sheetId':1}}]}
        if url.endswith(':batchUpdate'):return {}
        if '/values/' in url:
            if self.fail_report:raise GoogleError('simulated Sheets outage')
            if method=='PUT':self.sheet=kw['json']['values'];return {'updatedRows':len(self.sheet)}
            return {'values':[trim(r) for r in self.sheet if any(r)]}
        raise AssertionError(url)


def trim(row):
    row=list(row)
    while row and row[-1]=='':row.pop()
    return row


def message(mid,parts):
    return {'id':mid,'internalDate':'1793624400000','labelIds':['label-1'],
            'payload':{'mimeType':'multipart/mixed','parts':parts}}


def test_google_storage_separate_encrypted_and_owner_only(tmp_path):
    auth(tmp_path)
    assert GoogleStore(tmp_path).load()['mailbox']==MAIL
    assert not TokenStore(tmp_path).path.exists()
    assert b'access' not in GoogleStore(tmp_path).path.read_bytes()
    assert GoogleStore(tmp_path).path.stat().st_mode & 0o777==0o600


def test_google_authorization_scopes_and_wrong_mailbox_rejected(tmp_path):
    def handler(req):
        if str(req.url).endswith('/token'):
            return httpx.Response(200,json={'access_token':'access','refresh_token':'refresh','expires_in':3600,'scope':' '.join(SCOPES)})
        return httpx.Response(200,json={'emailAddress':'wrong@example.com'})
    g=google(tmp_path,httpx.MockTransport(handler))
    with pytest.raises(GoogleError,match='Wrong mailbox'):asyncio.run(g.authorize('code','verifier'))
    assert not g.store.path.exists()


def test_partial_google_consent_is_not_saved(tmp_path):
    g=google(tmp_path,httpx.MockTransport(lambda r:httpx.Response(200,json={'access_token':'access','refresh_token':'refresh','expires_in':3600,'scope':SCOPES[0]})))
    with pytest.raises(GoogleError,match='Both Gmail'):asyncio.run(g.authorize('code','verifier'))
    assert not g.store.path.exists()


def test_google_refresh_rotates_safely_and_errors_are_sanitized(tmp_path):
    auth(tmp_path);s=GoogleStore(tmp_path);r=s.load();r['expires_at']=1;s.save(r);calls=[]
    def handler(req):
        calls.append(req)
        if str(req.url).endswith('/token'):
            assert parse_qs(req.content.decode())['grant_type']==['refresh_token']
            return httpx.Response(200,json={'access_token':'new','expires_in':3600})
        assert req.headers['Authorization']=='Bearer new'
        return httpx.Response(403,text='secret-upstream-body')
    with pytest.raises(GoogleError) as exc:asyncio.run(google(tmp_path,httpx.MockTransport(handler)).request('GET',GMAIL+'/profile'))
    assert 'secret-upstream-body' not in str(exc.value)
    assert s.load()['access_token']=='new' and len(calls)==2


def test_mime_nested_inline_limits_and_invalid_base64():
    p=part(b'%PDF-test')
    assert attachments({'parts':[{'parts':[p]}]})==[p]
    inline=dict(p,filename='');assert attachments(inline)==[inline]
    with pytest.raises(GoogleError):attachments({'parts':[p]*21})
    with pytest.raises(GoogleError):decode_body({'data':'!!!'})
    with pytest.raises(GoogleError):decode_body({'data':'YWJj','size':4})


def test_delivery_identity_survives_restart_and_is_tenant_bound(tmp_path):
    store=MailStore(tmp_path);p=part(b'data')
    row,new=store.receive(TENANT,MAIL,'m1',p,1);assert new
    same,new=MailStore(tmp_path).receive(TENANT,MAIL,'m1',p,1);assert not new and same['id']==row['id']
    assert store.receive(TENANT,MAIL,'m2',p,1)[1]
    assert store.receive('other',MAIL,'m1',p,1)[1]
    assert len(store.all(TENANT,MAIL))==2


def test_poll_cache_duplicate_invalid_unsupported_and_repeat_no_new_calls(tmp_path,monkeypatch):
    blob=(ROOT/'fixtures/invoice_001.pdf').read_bytes();r=cache(tmp_path)
    fake=MailGoogle([message('m1',[part(blob)]),message('m2',[part(blob,'renamed.pdf')]),
        message('m3',[{'partId':'1','filename':'notes.txt','mimeType':'text/plain','body':{}}])])
    c=FakeConnection(tmp_path);intake=MailIntake(c,fake,asyncio.Lock())
    async def forbidden(*a,**k):raise AssertionError('cache must prevent model call')
    monkeypatch.setattr(intake.runs,'run',forbidden)
    first=asyncio.run(intake.poll());again=asyncio.run(intake.poll())
    rows=intake.store.all(TENANT,MAIL)
    assert first['new_deliveries']==3 and again['new_deliveries']==0
    assert rows[0]['status']=='verified' and rows[1]['duplicate_of']==rows[0]['id']
    assert rows[2]['status']=='unsupported'
    assert len([x for x in c.calls if x[:2]==('POST','/Invoices')])==1
    assert all(x['run_id']==r['id'] for x in rows[:2])


def test_invalid_cached_invoice_stays_held_and_makes_no_accounting_calls(tmp_path):
    value=invoice();value['invoice_number']=None;cache(tmp_path,['missing:invoice_number'],value)
    c=FakeConnection(tmp_path);fake=MailGoogle([message('m1',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())])])
    intake=MailIntake(c,fake,asyncio.Lock());asyncio.run(intake.poll())
    assert intake.store.all(TENANT,MAIL)[0]['status']=='held' and not c.calls


def test_interrupted_extraction_is_not_paid_again(tmp_path,monkeypatch):
    r=cache(tmp_path);runs=ExtractionRuns(tmp_path);r['status']='processing';runs.save(r)
    c=FakeConnection(tmp_path);fake=MailGoogle([message('m1',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())])])
    intake=MailIntake(c,fake,asyncio.Lock());asyncio.run(intake.poll());asyncio.run(intake.poll())
    assert intake.store.all(TENANT,MAIL)[0]['status']=='needs_inspection' and not c.calls


def test_practice_snapshot_cannot_poison_model_cache(tmp_path):
    real=cache(tmp_path);runs=ExtractionRuns(tmp_path)
    import uuid
    practice={**real,'id':uuid.uuid4().hex,'fixture_source_run':real['id'],'fixture_note':'injected missing price','status':'held'}
    runs.save(practice)
    assert runs.cached(real['source_sha256'],real['prompt_sha256'],'deepseek-flash')['id']==real['id']


def test_report_retry_never_replays_accounting_and_duplicate_total_counted_once(tmp_path):
    blob=(ROOT/'fixtures/invoice_001.pdf').read_bytes();cache(tmp_path)
    fake=MailGoogle([message('m1',[part(blob)]),message('m2',[part(blob,'copy.pdf')])])
    c=FakeConnection(tmp_path);intake=MailIntake(c,fake,asyncio.Lock());asyncio.run(intake.poll())
    before=list(c.calls);report=MonthlyReport(c,fake)
    fake.fail_report=True
    with pytest.raises(GoogleError):asyncio.run(report.refresh('2026-08'))
    assert report.store.report(TENANT,MAIL,'2026-08')['status']=='pending'
    fake.fail_report=False
    result=asyncio.run(report.refresh('2026-08'));assert result['status']=='verified'
    assert c.calls==before and fake.creates==1
    snapshot=report.snapshot(TENANT,'2026-08')
    assert len(snapshot)==3 and sum(row[8]==95.16 for row in snapshot[1:])==1
    assert [x[0] for x in fake.sheet]==[x[0] for x in snapshot]
    put=next(kw for method,url,kw in fake.calls if method=='PUT')
    assert put['params']['valueInputOption']=='RAW'


def test_report_month_fallback_and_sheet_create_uncertainty(tmp_path):
    c=FakeConnection(tmp_path);g=MailGoogle([]);report=MonthlyReport(c,g)
    report.store.receive(TENANT,MAIL,'m1',{'partId':'1','filename':'unsupported.txt'},1793624400)
    assert len(report.snapshot(TENANT,'2026-11'))==2
    assert len(report.snapshot(TENANT,'2026-08'))==1
    report.store.save_report(TENANT,MAIL,'2026-11','creating','creation unknown')
    with pytest.raises(GoogleError,match='creation was interrupted'):asyncio.run(report.refresh('2026-11'))
    assert g.creates==0 and not c.calls


def test_google_oauth_state_pkce_and_browser_form_guards(tmp_path,monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID','client');monkeypatch.setenv('GOOGLE_CLIENT_SECRET','secret');monkeypatch.setenv('GOOGLE_MAILBOX',MAIL)
    TokenStore(tmp_path).save({'access_token':'x','refresh_token':'r','expires_at':9999999999,'tenant_id':TENANT,'tenant_name':'Demo Company (UK)'})
    app=create_app(Settings('x','y',storage_dir=str(tmp_path)))
    with TestClient(app,base_url='http://localhost:8080') as client:
        start=client.get('/oauth/google/start',follow_redirects=False)
        q=parse_qs(__import__('urllib.parse',fromlist=['urlparse']).urlparse(start.headers['location']).query)
        assert q['access_type']==['offline'] and q['code_challenge_method']==['S256']
        assert set(q['scope'][0].split())==set(SCOPES)
        assert client.get('/oauth/google/callback?state=wrong&code=x').status_code==400
        assert client.get('/mail',headers={'sec-fetch-site':'cross-site'}).status_code==409
        assert client.post('/mail/poll',headers={'origin':'https://evil.example'}).status_code==409
        page=client.get('/mail');assert page.status_code==200
        csrf=re.search('name="csrf" value="([^"]+)"',page.text)[1]
        assert client.post('/mail/poll',data={'csrf':'wrong'},headers={'origin':'http://localhost:8080'}).status_code==409
        assert client.get('/oauth/google/callback?state='+q['state'][0]+'&error=access_denied',follow_redirects=False).status_code==303
        assert client.get('/oauth/google/callback?state='+q['state'][0]+'&error=access_denied').status_code==400


def test_poll_new_document_budget_prevents_extra_model_cost(tmp_path,monkeypatch):
    # Unique bounded PDFs, with a stubbed model, exercise the real intake budget.
    import io
    from pypdf import PdfReader,PdfWriter
    base=(ROOT/'fixtures/invoice_001.pdf').read_bytes();messages=[]
    for i in range(5):
        writer=PdfWriter(clone_from=PdfReader(io.BytesIO(base)))
        writer.add_metadata({'/Title':'Synthetic '+str(i)})
        out=io.BytesIO();writer.write(out)
        messages.append(message('m'+str(i),[part(out.getvalue())]))
    fake=MailGoogle(messages);c=FakeConnection(tmp_path);intake=MailIntake(c,fake,asyncio.Lock());calls=[]
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key')
    original=intake.runs.run
    async def stub(data,filename,key,**kwargs):
        import uuid
        calls.append(filename);rid=uuid.uuid4().hex
        r={'id':rid,'filename':filename,'status':'held','invoice':{},'issues':['provider unavailable'],
           'source_sha256':hashlib.sha256(data).hexdigest(),'prompt_sha256':hashlib.sha256(PROMPT.read_bytes()).hexdigest(),'requested_model':'deepseek-flash'}
        intake.runs.save(r);(intake.runs.folder(rid)/'source.pdf').write_bytes(data)
        return r
    monkeypatch.setattr(intake.runs,'run',stub)
    asyncio.run(intake.poll());asyncio.run(intake.poll())
    assert len(calls)==3
    assert sum(r['status']=='needs_inspection' for r in intake.store.all(TENANT,MAIL))==2
    assert not c.calls


def test_label_changed_message_not_processed(tmp_path):
    m=message('m1',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())]);m['labelIds']=[]
    c=FakeConnection(tmp_path);intake=MailIntake(c,MailGoogle([m]),asyncio.Lock())
    assert asyncio.run(intake.poll())['new_deliveries']==0
    assert not intake.store.all(TENANT,MAIL)


def test_oversized_attachment_held_without_download_or_model(tmp_path):
    p={'partId':'1','filename':'large.pdf','mimeType':'application/pdf','body':{'size':10000001,'attachmentId':'large'}}
    c=FakeConnection(tmp_path);g=MailGoogle([message('m1',[p])]);intake=MailIntake(c,g,asyncio.Lock())
    asyncio.run(intake.poll())
    assert intake.store.all(TENANT,MAIL)[0]['status']=='unsupported'
    assert not any('/attachments/' in x[1] for x in g.calls)
    assert not c.calls


def test_report_shrinking_snapshot_clears_stale_rows(tmp_path):
    cache(tmp_path);blob=(ROOT/'fixtures/invoice_001.pdf').read_bytes()
    c=FakeConnection(tmp_path);g=MailGoogle([message('m1',[part(blob)]),message('m2',[part(blob)])])
    intake=MailIntake(c,g,asyncio.Lock());asyncio.run(intake.poll());report=MonthlyReport(c,g)
    asyncio.run(report.refresh('2026-08'))
    row=intake.store.all(TENANT,MAIL)[1]
    # Simulate a delivery moved to receipt-month classification by unlinking its case.
    intake.store.update(row['id'],'unsupported','Test moved source classification',run_id=None)
    asyncio.run(report.refresh('2026-08'))
    assert len(g.sheet)==3 and g.sheet[-1]==['']*18
    assert g.creates==1


def test_source_hash_tamper_prevents_cached_accounting(tmp_path):
    r=cache(tmp_path);runs=ExtractionRuns(tmp_path);(runs.folder(r['id'])/'source.pdf').write_bytes(b'tampered')
    c=FakeConnection(tmp_path);g=MailGoogle([message('m1',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())])])
    intake=MailIntake(c,g,asyncio.Lock());asyncio.run(intake.poll())
    assert intake.store.all(TENANT,MAIL)[0]['status']=='needs_inspection' and not c.calls


def test_report_lock_excludes_other_writer(tmp_path):
    a=MailStore(tmp_path);b=MailStore(tmp_path)
    with a.gate('report'):
        with pytest.raises(GoogleError,match='Another report'):
            with b.gate('report'):pass


def test_lost_creation_response_can_adopt_only_matching_report(tmp_path):
    c=FakeConnection(tmp_path);g=MailGoogle([]);report=MonthlyReport(c,g)
    report.store.save_report(TENANT,MAIL,'2026-08','creating','lost response')
    original=g.request
    async def request(method,url,**kw):
        if url==SHEETS+'/existing-sheet-id':
            return {'properties':{'title':'Northmere synthetic invoice report 2026-08'},'sheets':[{'properties':{'title':'Intake','sheetId':1}}]}
        return await original(method,url,**kw)
    g.request=request
    asyncio.run(report.adopt('2026-08','existing-sheet-id'))
    assert report.store.report(TENANT,MAIL,'2026-08')['spreadsheet_id']=='existing-sheet-id'
    assert asyncio.run(report.refresh('2026-08'))['status']=='verified'
    assert not g.creates and not c.calls


def test_approval_required_applies_to_gmail_intake(tmp_path):
    cache(tmp_path);store=ReviewStore(tmp_path);store.set_policy(TENANT,'required','Verify Gmail follows approval policy')
    c=FakeConnection(tmp_path);g=MailGoogle([message('m1',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())])])
    intake=MailIntake(c,g,asyncio.Lock());asyncio.run(intake.poll())
    assert intake.store.all(TENANT,MAIL)[0]['status']=='awaiting_approval' and not c.calls


def test_report_readback_mismatch_stays_pending(tmp_path):
    c=FakeConnection(tmp_path);g=MailGoogle([]);report=MonthlyReport(c,g)
    original=g.request
    async def request(method,url,**kw):
        if method=='GET' and '/values/' in url:return {'values':[['wrong header']]}
        return await original(method,url,**kw)
    g.request=request
    with pytest.raises(GoogleError,match='read-back differs'):asyncio.run(report.refresh('2026-08'))
    assert report.store.report(TENANT,MAIL,'2026-08')['status']=='pending'
    assert not c.calls


def test_receipt_with_external_attachment_download_is_linked(tmp_path):
    cache(tmp_path);data=(ROOT/'fixtures/invoice_001.pdf').read_bytes()
    p={'partId':'1','filename':'invoice.pdf','mimeType':'application/pdf','body':{'attachmentId':'attachment-1','size':len(data)}}
    g=MailGoogle([message('m1',[p])]);original=g.request
    async def request(method,url,**kw):
        if url.endswith('/attachments/attachment-1'):return {'data':base64.urlsafe_b64encode(data).decode(),'size':len(data)}
        return await original(method,url,**kw)
    g.request=request;c=FakeConnection(tmp_path);intake=MailIntake(c,g,asyncio.Lock());asyncio.run(intake.poll())
    row=intake.store.all(TENANT,MAIL)[0]
    assert row['attachment_id']=='attachment-1' and row['status']=='verified'


def test_resume_reuses_completed_run_after_delivery_link_interruption(tmp_path,monkeypatch):
    r=cache(tmp_path);data=(ROOT/'fixtures/invoice_001.pdf').read_bytes();p=part(data)
    c=FakeConnection(tmp_path);g=MailGoogle([message('m1',[p])]);intake=MailIntake(c,g,asyncio.Lock())
    row,_=intake.store.receive(TENANT,MAIL,'m1',p,1793624400)
    intake.store.update(row['id'],'extracting','Interrupted before receipt link',source_sha256=r['source_sha256'])
    async def forbidden(*a,**kw):raise AssertionError('No paid retry')
    monkeypatch.setattr(intake.runs,'run',forbidden)
    asyncio.run(intake.resume(row['id']))
    assert intake.store.get(row['id'])['status']=='verified'


def test_oauth_success_saves_expected_mailbox(tmp_path):
    def handler(req):
        if str(req.url).endswith('/token'):
            body=parse_qs(req.content.decode())
            assert body['code_verifier']==['verifier']
            assert body['redirect_uri']==['http://localhost:8080/oauth/google/callback']
            return httpx.Response(200,json={'access_token':'access','refresh_token':'refresh','expires_in':3600,'scope':' '.join(SCOPES)})
        return httpx.Response(200,json={'emailAddress':MAIL})
    g=google(tmp_path,httpx.MockTransport(handler));assert asyncio.run(g.authorize('code','verifier'))==MAIL
    assert g.store.load()['mailbox']==MAIL


def test_poll_cursor_survives_restart_and_cycles_label_pages(tmp_path):
    c=FakeConnection(tmp_path);g=MailGoogle([]);tokens=[]
    original=g.request
    async def request(method,url,**kw):
        if url==GMAIL+'/messages':
            token=kw['params'].get('pageToken');tokens.append(token)
            return {'messages':[],'nextPageToken':'page-two'} if token is None else {'messages':[]}
        return await original(method,url,**kw)
    g.request=request
    first=MailIntake(c,g,asyncio.Lock());assert asyncio.run(first.poll())['more_pages']
    second=MailIntake(c,g,asyncio.Lock());assert not asyncio.run(second.poll())['more_pages']
    asyncio.run(second.poll())
    assert tokens==[None,'page-two',None]


def test_rotating_gmail_attachment_locator_does_not_create_another_delivery(tmp_path):
    store=MailStore(tmp_path)
    first,_=store.receive(TENANT,MAIL,'m1',{'partId':'1','filename':'invoice.pdf','body':{'attachmentId':'locator-1'}},1)
    again,new=store.receive(TENANT,MAIL,'m1',{'partId':'1','filename':'invoice.pdf','body':{'attachmentId':'locator-2'}},1)
    assert not new and first['id']==again['id']
    assert len(store.all(TENANT,MAIL))==1


def test_prototype_rotating_locator_rows_are_preserved_as_superseded_audit(tmp_path):
    store=MailStore(tmp_path)
    # Reconstruct the pre-fix prototype schema containing two locators for one MIME part.
    with store.db() as db:
        db.execute('DROP INDEX delivery_identity')
        db.execute('ALTER TABLE deliveries DROP COLUMN identity_key')
        for i in range(2):
            db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('old-'+str(i),TENANT,MAIL,'m1','1','locator-'+str(i),'invoice.pdf',1,i,'verified','prototype',None,None,None))
    migrated=MailStore(tmp_path)
    assert [r['id'] for r in migrated.all(TENANT,MAIL)]==['old-0']
    with migrated.db() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM deliveries ORDER BY created')]
    assert len(rows)==2 and rows[1]['status']=='superseded' and rows[1]['duplicate_of']=='old-0'


def test_live_verifier_reads_the_actual_accounting_ledger(tmp_path):
    from scripts.verify_google_demo import counts
    cache(tmp_path);c=FakeConnection(tmp_path)
    intake=MailIntake(c,MailGoogle([message('m1',[part((ROOT/'fixtures/invoice_001.pdf').read_bytes())])]),asyncio.Lock())
    asyncio.run(intake.poll())
    result=counts(tmp_path,TENANT)
    assert result['ledger_invoices']==1 and result['bill_creation_intents']==1
    assert not (tmp_path/'invoices.sqlite3').exists()
