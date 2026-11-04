import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from app.ledger import Ledger
from app.pipeline import InvoicePipeline
from app.samples import sample
from test_bills import FakeConnection


def submit(p,key,source=None):
    inv,blob,name=sample(key)
    return asyncio.run(p.submit(inv,blob,name,source))

def test_source_poll_idempotency_and_hash_receipts(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c)
    first=submit(p,'original','mailbox:message:part')
    repeated=submit(p,'original','mailbox:message:part')
    renamed=submit(p,'renamed')
    assert first['receipt_id']==repeated['receipt_id']
    assert renamed['receipt_outcome']=='duplicate_file'
    snap=p.ledger.snapshot('uk-demo')
    assert len(snap['receipts'])==2 and len(snap['invoices'])==1 and len(snap['attempts'])==1
    assert len(list((tmp_path/'ledger/documents').glob('*.pdf')))==1

def test_reexport_business_duplicate(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c)
    a=submit(p,'original');b=submit(p,'reexport')
    assert a['sha256']!=b['sha256']
    assert b['receipt_outcome']=='duplicate_invoice' and b['invoice_id']==a['invoice_id']

def test_conflict_preserves_original_and_never_creates(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c)
    a=submit(p,'original');b=submit(p,'conflict')
    assert b['status']=='held_conflict'
    original=p.ledger.invoice(a['ledger_invoice_id'])
    assert json.loads(original['expected_json'])['displayed_total']=='95.16'
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_unknown_and_invalid_preserved_without_xero_calls(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c)
    assert submit(p,'unknown')['status']=='held_invalid'
    assert submit(p,'invalid')['status']=='held_invalid'
    assert len(p.ledger.snapshot('uk-demo')['receipts'])==2
    assert not c.calls

def test_two_independent_invoices_can_be_registered(tmp_path):
    ledger=Ledger(tmp_path);a,blob,name=sample('original');b,blob2,name2=sample('second')
    r1=ledger.receive('t','s1',name,blob,a,'alder')
    r2=ledger.receive('t','s2',name2,blob2,b,'alder')
    assert r1['invoice_id']!=r2['invoice_id']
    assert len(ledger.snapshot('t')['invoices'])==2

def test_database_uniqueness_under_parallel_receipts(tmp_path):
    ledger=Ledger(tmp_path);inv,blob,name=sample('original')
    def run(n):return ledger.receive('t',str(n),name,blob,inv,'alder')
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(run,range(12)))
    assert len({r['invoice_id'] for r in rows})==1
    assert len(ledger.snapshot('t')['receipts'])==12

def test_os_lock_is_exclusive_and_released(tmp_path):
    first=Ledger(tmp_path);second=Ledger(tmp_path)
    with first.lock('same') as got:
        assert got
        with second.lock('same') as other:assert not other
    with second.lock('same') as other:assert other

def test_restart_after_lost_creation_response(tmp_path):
    c=FakeConnection(tmp_path);c.uncertain_create=True
    initial=submit(InvoicePipeline(c),'original');assert initial['status']=='uncertain'
    recovered=submit(InvoicePipeline(c),'reexport')
    assert recovered['status']=='verified' and recovered['reused_existing']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_persisted_intent_without_bill_prevents_recreate_after_restart(tmp_path):
    c=FakeConnection(tmp_path);c.fail_create=True
    first=submit(InvoicePipeline(c),'original');assert first['status']=='uncertain'
    c.fail_create=False
    again=submit(InvoicePipeline(c),'renamed');assert again['status']=='uncertain'
    assert 'no blind' in again['message']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_deleted_destination_not_silently_recreated(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c);submit(p,'original');c.invoices=[]
    again=submit(p,'original')
    assert again['status']=='uncertain'
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_tenant_isolation(tmp_path):
    ledger=Ledger(tmp_path);inv,blob,name=sample('original')
    a=ledger.receive('company-a','s',name,blob,inv,'alder')
    b=ledger.receive('company-b','s',name,blob,inv,'alder')
    assert a['invoice_id']!=b['invoice_id']
    assert len(ledger.snapshot('company-a')['receipts'])==1

def test_source_id_cannot_be_reassigned(tmp_path):
    l=Ledger(tmp_path);inv,blob,name=sample('original')
    l.receive('t','same',name,blob,inv,'alder')
    with pytest.raises(ValueError):l.receive('t','same',name,blob+b'changed',inv,'alder')
    changed=copy.deepcopy(inv);changed['displayed_total']='123.45'
    with pytest.raises(ValueError):l.receive('t','same',name,blob,changed,'alder')

def test_case_variant_held_as_conflict(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c);submit(p,'original')
    inv,blob,name=sample('original');inv['invoice_number']=inv['invoice_number'].lower()
    r=asyncio.run(p.submit(inv,blob,name));assert r['status']=='held_conflict'

def test_conflicting_snapshot_is_preserved(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c);submit(p,'original');submit(p,'conflict')
    receipts=p.ledger.snapshot('uk-demo')['receipts']
    conflict=next(r for r in receipts if r['outcome']=='held_conflict')
    assert json.loads(conflict['submitted_json'])['displayed_total']=='97.56'

def test_lock_released_after_process_exit(tmp_path):
    import subprocess,sys
    code="from app.ledger import Ledger; import os,sys; l=Ledger(sys.argv[1]); ctx=l.lock('crash'); assert ctx.__enter__(); os._exit(0)"
    subprocess.run([sys.executable,'-c',code,str(tmp_path)],check=True)
    with Ledger(tmp_path).lock('crash') as got:assert got

def test_busy_invoice_does_not_make_upstream_call(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c)
    inv,blob,name=sample('original');receipt=p.ledger.receive('uk-demo','source',name,blob,inv,'alder-packing')
    with p.ledger.lock('invoice:'+receipt['invoice_id']):
        result=asyncio.run(p.process(receipt['invoice_id']))
    assert result['status']=='processing' and not c.calls

def test_ledger_ui_and_submission_protection(tmp_path):
    from app.main import create_app,Settings
    from fastapi.testclient import TestClient
    c=TestClient(create_app(Settings('id','secret',storage_dir=str(tmp_path))))
    assert c.post('/ledger/submit').status_code==403
    assert c.get('/ledger/data').status_code==409

def test_additive_receipt_migration(tmp_path):
    import sqlite3
    l=Ledger(tmp_path)
    with sqlite3.connect(l.path) as db:
        db.execute('ALTER TABLE receipts DROP COLUMN submitted_json')
        db.execute('DELETE FROM schema_version');db.execute('INSERT INTO schema_version VALUES(1)')
    l=Ledger(tmp_path)
    with l.db() as db:
        assert 'submitted_json' in [r['name'] for r in db.execute('PRAGMA table_info(receipts)')]
        assert db.execute('SELECT version FROM schema_version').fetchone()[0]==2

def test_two_invoices_create_and_repeat_independently(tmp_path):
    c=FakeConnection(tmp_path);p=InvoicePipeline(c)
    a=submit(p,'original');b=submit(p,'second');again=submit(p,'original')
    assert a['status']==b['status']==again['status']=='verified'
    assert a['invoice_id']!=b['invoice_id'] and again['invoice_id']==a['invoice_id']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==2

def test_overlapping_pipeline_instances_have_one_writer(tmp_path):
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        class SlowConnection(FakeConnection):
            async def request(self,method,path,**kwargs):
                if method=='POST' and path=='/Invoices':entered.set();await release.wait()
                return await super().request(method,path,**kwargs)
        c=SlowConnection(tmp_path);p1=InvoicePipeline(c);p2=InvoicePipeline(c)
        inv,blob,name=sample('original')
        task=asyncio.create_task(p1.submit(inv,blob,name))
        await asyncio.wait_for(entered.wait(),2)
        busy=await p2.submit(inv,blob,name)
        assert busy['status']=='processing'
        release.set();done=await task
        assert done['status']=='verified'
        assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1
    asyncio.run(run())

def test_newer_schema_not_downgraded(tmp_path):
    l=Ledger(tmp_path)
    with l.db() as db:db.execute('UPDATE schema_version SET version=99')
    with pytest.raises(ValueError,match='newer'):Ledger(tmp_path)

def test_browser_form_integration(monkeypatch,tmp_path):
    import re
    from fastapi.testclient import TestClient
    from app.main import create_app,Settings
    from app.connection import TokenStore
    from test_connection import record
    fake=FakeConnection(tmp_path)
    monkeypatch.setattr('app.main.XeroConnection',lambda *args:fake)
    c=TestClient(create_app(Settings('id','secret',storage_dir=str(tmp_path))),follow_redirects=False)
    page=c.get('/ledger');assert page.status_code==200
    token=re.search('name="csrf" value="([^"]+)"',page.text).group(1)
    r=c.post('/ledger/submit',headers={'Origin':'http://localhost:8080'},data={'csrf':token,'sample':'original'})
    assert r.status_code==303 and r.headers['location']=='/ledger'
    assert c.get('/ledger/data').json()['invoices'][0]['state']=='verified'
    assert 'AP-2026-1001' in c.get('/ledger').text
