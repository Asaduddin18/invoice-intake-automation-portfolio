import asyncio
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.extraction import ExtractionError
from app.extraction_runs import ExtractionRuns
from app.review import ReviewStore, ReviewError, DEFAULT_MAPPING
from app.workflow import submit_case
from app.pipeline import InvoicePipeline
from test_bills import FakeConnection

TENANT='uk-demo'
ROOT=Path(__file__).parents[1]


def invoice():
    data=json.loads((ROOT/'fixtures/invoice_001.json').read_text())
    keys=('supplier','invoice_number','invoice_date','due_date','currency','line_items','subtotal','tax','displayed_total')
    value={key:data[key] for key in keys}
    value['supplier']={key:data['supplier'][key] for key in ('name','email','address')}
    return value


def seed(directory, candidate=None, issues=None):
    runs=ExtractionRuns(directory)
    run_id=uuid.uuid4().hex
    source=(ROOT/'fixtures/invoice_001.pdf').read_bytes()
    record={'id':run_id,'filename':'invoice_001.pdf','status':'held' if issues else 'extracted_valid',
        'invoice':candidate if candidate is not None else invoice(),'issues':issues or [],'attempts':[],
        'source_sha256':hashlib.sha256(source).hexdigest(),'fixture_note':'Synthetic extraction snapshot for workflow tests.'}
    runs.save(record)
    (runs.folder(run_id)/'source.pdf').write_bytes(source)
    return ReviewStore(directory).ensure_case(TENANT,run_id)


def approve(store,case):
    store.approve(TENANT,case['id'],case['revision'],store.signature(TENANT,case))


def submit(connection,case):
    return asyncio.run(submit_case(connection,case['id'],case['revision']))


def test_automatic_mode_needs_no_approval_and_duplicate_retry_is_safe(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path);connection=FakeConnection(tmp_path)
    assert store.policy(TENANT)['mode']=='automatic'
    first=submit(connection,case);again=submit(connection,case)
    assert first['status']=='verified' and again['reused_existing']
    assert len([x for x in connection.calls if x[:2]==('POST','/Invoices')])==1
    with pytest.raises(ReviewError,match='accounting operation'):
        store.revise(TENANT,case['id'],1,invoice(),'Attempt to modify a posted source',True)


def test_required_mode_blocks_legacy_pipeline_before_registration_or_network(tmp_path):
    store=ReviewStore(tmp_path);store.set_policy(TENANT,'required','Test approval mode')
    connection=FakeConnection(tmp_path)
    pipeline=InvoicePipeline(connection)
    result=asyncio.run(pipeline.submit(invoice(),(ROOT/'fixtures/invoice_001.pdf').read_bytes(),'source.pdf'))
    assert result['status']=='awaiting_review' and not connection.calls
    assert pipeline.ledger.snapshot(TENANT)['invoices']==[]


def test_required_approval_is_revision_bound_and_edit_revokes_it(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path);connection=FakeConnection(tmp_path)
    store.set_policy(TENANT,'required','Demonstrate approval policy')
    assert submit(connection,case)['status']=='awaiting_approval' and not connection.calls
    approve(store,case)
    assert store.decision(TENANT,store.get(TENANT,case['id']))[0]=='ready'
    changed=invoice();changed['due_date']='2026-08-18'
    new=store.revise(TENANT,case['id'],1,changed,'Correct date after checking source evidence',True)
    assert new['revision']==2 and new['approval'] is None
    assert submit(connection,new)['status']=='awaiting_approval' and not connection.calls
    approve(store,new)
    assert submit(connection,new)['status']=='verified'
    assert ExtractionRuns(tmp_path).load(case['id'])['invoice']['due_date']=='2026-08-17'
    assert any(e['kind']=='corrected' for e in store.history(TENANT,case['id']))


@pytest.mark.parametrize('mode',['automatic','required'])
def test_invalid_amount_never_posts_and_cannot_be_approved(tmp_path,mode):
    bad=invoice();bad['displayed_total']='999.99'
    case=seed(tmp_path,bad,['arithmetic:total']);store=ReviewStore(tmp_path)
    store.set_policy(TENANT,mode,'Verify invalid amounts stay held')
    connection=FakeConnection(tmp_path)
    assert submit(connection,case)['status']=='held' and not connection.calls
    with pytest.raises(ReviewError,match='Resolve validation'):
        approve(store,case)


def test_correction_revalidates_and_keeps_original_extraction(tmp_path):
    bad=invoice();bad['line_items'][0]['unit_price']=None
    case=seed(tmp_path,bad,['invalid:numeric_fields']);store=ReviewStore(tmp_path)
    assert store.decision(TENANT,case)[0]=='held'
    corrected=store.revise(TENANT,case['id'],1,invoice(),'Copied printed unit price from source PDF',True)
    assert store.decision(TENANT,corrected)[0]=='ready'
    assert ExtractionRuns(tmp_path).load(case['id'])['invoice']['line_items'][0]['unit_price'] is None
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==2


def test_disagreement_requires_explicit_source_checked_resolution(tmp_path):
    case=seed(tmp_path,issues=['text_vision_disagreement']);store=ReviewStore(tmp_path)
    assert store.decision(TENANT,case)[0]=='held'
    with pytest.raises(ReviewError):store.revise(TENANT,case['id'],1,invoice(),'Resolve reading disagreement',False)
    resolved=store.revise(TENANT,case['id'],1,invoice(),'Verified all candidate fields against source',True)
    assert resolved['unresolved']==[] and store.decision(TENANT,resolved)[0]=='ready'


def test_stale_revision_or_approval_form_fails(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    signature=store.signature(TENANT,case)
    store.set_policy(TENANT,'required','Policy changed after page was opened')
    with pytest.raises(ReviewError,match='Policy or mapping changed'):
        store.approve(TENANT,case['id'],1,signature)
    store.revise(TENANT,case['id'],1,invoice(),'Record a source-checked resolution',True)
    with pytest.raises(ReviewError,match='stale'):
        store.revise(TENANT,case['id'],1,invoice(),'Try overwriting a newer revision',True)
    with pytest.raises(ReviewError,match='Stale'):
        store.approve(TENANT,case['id'],1,signature)


def test_mapping_change_invalidates_prior_approval(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    store.set_policy(TENANT,'required','Require approval for mapping test');approve(store,case)
    store.save_mapping(TENANT,'Alder Packing Supplies Ltd','326','INPUT2','0.20','Explicit accounting mapping change')
    assert store.decision(TENANT,store.get(TENANT,case['id']))[0]=='awaiting_approval'


def test_policy_switch_invalidates_old_approval_and_survives_restart(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    store.set_policy(TENANT,'required','Require approval');approve(store,case)
    store.set_policy(TENANT,'automatic','Return to automatic test mode')
    store.set_policy(TENANT,'required','Enable required mode again')
    reopened=ReviewStore(tmp_path)
    assert reopened.decision(TENANT,reopened.get(TENANT,case['id']))[0]=='awaiting_approval'
    assert len(reopened.history(TENANT,case['id']))>=2


def test_source_tampering_and_wrong_tenant_block_access(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    assert store.get('another-tenant',case['id']) is None
    (ExtractionRuns(tmp_path).folder(case['id'])/'source.pdf').write_bytes(b'changed')
    with pytest.raises(ExtractionError):approve(store,case)
    with pytest.raises(ExtractionError):submit(FakeConnection(tmp_path),case)


def test_approval_cannot_authorize_another_invoice_or_source(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    store.set_policy(TENANT,'required','Required policy');approve(store,case)
    token={'case_id':case['id'],'revision':1}
    changed=invoice();changed['invoice_number']='DIFFERENT'
    for value,source in [(changed,case['source_sha256']),(invoice(),'wrong-hash')]:
        with pytest.raises(ReviewError,match='does not match'):
            store.authorize(TENANT,value,source,token)


def test_uncertain_write_reconciles_without_second_creation(tmp_path):
    case=seed(tmp_path);connection=FakeConnection(tmp_path);connection.uncertain_create=True
    assert submit(connection,case)['status']=='uncertain'
    store=ReviewStore(tmp_path)
    with pytest.raises(ReviewError):store.revise(TENANT,case['id'],1,invoice(),'Try changing uncertain operation',True)
    result=submit(connection,case)
    assert result['status']=='verified' and result['reused_existing']
    assert len([x for x in connection.calls if x[:2]==('POST','/Invoices')])==1


def test_workflow_gate_blocks_policy_edit_mapping_and_correction_during_write(tmp_path):
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    with store.gate():
        for operation in [lambda:store.set_policy(TENANT,'required','Change while write active'),
                lambda:store.revise(TENANT,case['id'],1,invoice(),'Edit while write active',True),
                lambda:store.save_mapping(TENANT,'Alder Packing Supplies Ltd','325','INPUT2','0.20','Mapping while write active')]:
            with pytest.raises(ReviewError,match='active'):operation()


def test_explicit_unknown_supplier_mapping_and_accounting_validation(tmp_path):
    value=invoice();value['supplier']['name']='Dovetail Trade Supplies Ltd'
    case=seed(tmp_path,value);store=ReviewStore(tmp_path)
    assert store.decision(TENANT,case)[0]=='held'
    store.save_mapping(TENANT,value['supplier']['name'],'325','INPUT2','0.20','Explicitly map the fictional test supplier')
    assert store.decision(TENANT,store.get(TENANT,case['id']))[0]=='ready'
    connection=FakeConnection(tmp_path)
    assert submit(connection,case)['status']=='verified'
    assert connection.contacts[0]['Name']=='Dovetail Trade Supplies Ltd'
    assert connection.contacts[0]['ContactNumber']=='PORTFOLIO-DOVETAIL-001'
    with pytest.raises(ReviewError):store.save_mapping(TENANT,'Real company','325','INPUT2','0.20','Forbidden outside synthetic scope')


def client_app(tmp_path,monkeypatch):
    import app.main as module
    connection=FakeConnection(tmp_path)
    monkeypatch.setattr(module,'XeroConnection',lambda *args:connection)
    async def no_model(*args,**kwargs):raise AssertionError('Phase 5 must not make model calls')
    monkeypatch.setattr('app.extraction.model_call',no_model)
    return TestClient(module.create_app(module.Settings('id','secret',storage_dir=str(tmp_path)))),connection


def token(client,path='/review'):
    response=client.get(path)
    assert response.status_code==200
    return re.search('name="csrf" value="([^"]+)"',response.text).group(1)


def post(client,path,csrf,**values):
    return client.post(path,data={'csrf':csrf,**values},headers={'origin':'http://localhost:8080'},follow_redirects=False)


def test_browser_policy_intake_approval_source_and_no_model_calls(tmp_path,monkeypatch):
    case=seed(tmp_path);client,connection=client_app(tmp_path,monkeypatch)
    csrf=token(client)
    assert post(client,'/review/policy',csrf,mode='required',reason='Test required approval mode').status_code==303
    assert post(client,'/review/intake',csrf,run=case['id']).status_code==303
    assert not connection.calls
    page=client.get('/review/'+case['id'])
    assert 'Awaiting approval' in page.text and 'Source evidence' in page.text
    assert client.get('/review/'+case['id']+'/source').content.startswith(b'%PDF')
    assert client.get('/review/'+case['id']+'/page/1').content.startswith(b'\x89PNG')
    store=ReviewStore(tmp_path);csrf=token(client,'/review/'+case['id'])
    assert post(client,'/review/'+case['id']+'/approve',csrf,revision=1,signature=store.signature(TENANT,store.get(TENANT,case['id']))).status_code==303
    assert post(client,'/review/'+case['id']+'/submit',csrf,revision=1).status_code==303
    assert store.get(TENANT,case['id'])['outcome']['status']=='verified'


def test_review_routes_reject_csrf_cross_site_stale_forms_and_inactive_mapping(tmp_path,monkeypatch):
    case=seed(tmp_path);client,connection=client_app(tmp_path,monkeypatch)
    assert client.post('/review/policy',data={'mode':'required'}).status_code==403
    assert post(client,'/review/policy','bad',mode='required',reason='Test policy').status_code==403
    assert client.get('/review',headers={'sec-fetch-site':'cross-site'}).status_code==409
    csrf=token(client,'/review/'+case['id'])
    assert post(client,'/review/'+case['id']+'/approve',csrf,revision=99,signature='wrong').status_code==409
    connection.account='ARCHIVED'
    assert post(client,'/review/'+case['id']+'/mapping',csrf,revision=1,account_code='325',tax_type='INPUT2',tax_rate='0.20',reason='Validate inactive account mapping').status_code==409
    assert not any(x[0]=='POST' for x in connection.calls)


@pytest.mark.parametrize('mode',['automatic','required'])
def test_existing_legacy_bill_metadata_does_not_break_workflow_authorization(tmp_path,mode):
    connection=FakeConnection(tmp_path)
    legacy=json.loads((ROOT/'fixtures/invoice_001.json').read_text())
    source=(ROOT/'fixtures/invoice_001.pdf').read_bytes()
    prior=asyncio.run(InvoicePipeline(connection).submit(legacy,source,'legacy.pdf'))
    assert prior['status']=='verified'
    case=seed(tmp_path);store=ReviewStore(tmp_path)
    store.set_policy(TENANT,mode,'Reuse a bill from an earlier phase')
    if mode=='required':approve(store,case)
    result=submit(connection,case)
    assert result['status']=='verified' and result['reused_existing']
    assert len([x for x in connection.calls if x[:2]==('POST','/Invoices')])==1


def test_approved_page_uses_current_state_instead_of_previous_waiting_result(tmp_path):
    from app.review_view import detail
    case=seed(tmp_path);store=ReviewStore(tmp_path);connection=FakeConnection(tmp_path)
    store.set_policy(TENANT,'required','Approval UI test')
    assert submit(connection,case)['status']=='awaiting_approval'
    approve(store,case)
    html=detail(store,TENANT,store.get(TENANT,case['id']),'csrf')
    assert 'Ready to process · revision 1' in html


def test_browser_correction_revalidates_preserves_original_and_requires_new_approval(tmp_path,monkeypatch):
    bad=invoice();bad['line_items'][0]['unit_price']=None
    case=seed(tmp_path,bad,['invalid:numeric_fields']);client,connection=client_app(tmp_path,monkeypatch)
    store=ReviewStore(tmp_path);store.set_policy(TENANT,'required','Require approval for correction test')
    csrf=token(client,'/review/'+case['id'])
    values={key:value for key,value in invoice().items() if key not in ('supplier','line_items')}
    values.update({'supplier_'+key:value for key,value in invoice()['supplier'].items()})
    for i,line in enumerate(invoice()['line_items']):
        values.update({'line_'+str(i)+'_'+key:value for key,value in line.items()})
    values.update(revision=1,line_count=2,source_checked='yes',reason='Correct the unit price from the PDF')
    response=post(client,'/review/'+case['id']+'/correct',csrf,**values)
    assert response.status_code==303 and not connection.calls
    corrected=store.get(TENANT,case['id'])
    assert corrected['revision']==2 and store.decision(TENANT,corrected)[0]=='awaiting_approval'
    assert ExtractionRuns(tmp_path).load(case['id'])['invoice']['line_items'][0]['unit_price'] is None
    assert post(client,'/review/'+case['id']+'/correct',csrf,**values).status_code==409


def test_explicit_zero_tax_mapping_is_validated_and_used_in_payload(tmp_path):
    from app.bills import DemoBills,build_payload
    class ZeroTaxConnection(FakeConnection):
        async def request(self,method,path,**kwargs):
            if path=='/TaxRates':
                return {'TaxRates':[{'TaxType':'NONE','Name':'No VAT','Status':'ACTIVE','EffectiveRate':0,'CanApplyToExpenses':True}]}
            return await super().request(method,path,**kwargs)
    value=invoice();value['tax']='0.00';value['displayed_total']=value['subtotal']
    for line in value['line_items']:line['tax_rate']='0'
    mapping={**DEFAULT_MAPPING,'tax_type':'NONE','tax_rate':'0'}
    checker=DemoBills(ZeroTaxConnection(tmp_path),mapping);checker.invoice=value
    assert asyncio.run(checker.preview())['mapping']['tax_type']=='NONE'
    assert all(line['TaxType']=='NONE' for line in build_payload(value,'contact-id',mapping)['LineItems'])


def test_gate_result_cannot_unlock_financial_fields_after_an_accounting_operation(tmp_path):
    case=seed(tmp_path);connection=FakeConnection(tmp_path);store=ReviewStore(tmp_path)
    assert submit(connection,case)['status']=='verified'
    store.set_policy(TENANT,'required','Require fresh approval for later processing')
    assert submit(connection,case)['status']=='awaiting_approval'
    current=store.get(TENANT,case['id'])
    assert current['financial_locked']
    with pytest.raises(ReviewError,match='accounting operation'):
        store.revise(TENANT,case['id'],1,invoice(),'Try editing after a blocked reconciliation',True)
