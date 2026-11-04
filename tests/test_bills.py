import asyncio
import copy
import json
from types import SimpleNamespace
import pytest
from app.bills import DemoBills, FIXTURE, build_payload, validate, bill_matches
from app.connection import ConnectionError

class FakeConnection:
    def __init__(self,path):
        self.store=SimpleNamespace(directory=path,load=lambda:{'tenant_id':'uk-demo'})
        self.contacts=[];self.invoices=[];self.calls=[];self.fail_create=False;self.uncertain_create=False
        self.account='ACTIVE'
    async def request(self,method,path,*,body=None,params=None,idempotency_key=None):
        self.calls.append((method,path,body,idempotency_key))
        if path=='/Organisation':return {'Organisations':[{'CountryCode':'GB','BaseCurrency':'GBP'}]}
        if path=='/Accounts':return {'Accounts':[{'Code':'325','Name':'Direct Expenses','Type':'DIRECTCOSTS','Status':self.account}]}
        if path=='/TaxRates':return {'TaxRates':[{'TaxType':'INPUT2','Name':'20% VAT on Expenses','Status':'ACTIVE','EffectiveRate':20,'CanApplyToExpenses':True}]}
        if path=='/Contacts':
            if method=='GET':return {'Contacts':self.contacts}
            assert idempotency_key
            self.contacts=[dict(body['Contacts'][0],ContactID='contact-id',ContactStatus='ACTIVE')]
            return {'Contacts':self.contacts}
        if path=='/Invoices':
            if method=='GET':return {'Invoices':self.invoices}
            assert idempotency_key
            if self.fail_create:raise ConnectionError('Network outcome uncertain')
            from decimal import Decimal,ROUND_HALF_UP
            lines=body['Invoices'][0]['LineItems']
            net=sum((Decimal(str(x['Quantity']))*Decimal(str(x['UnitAmount']))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) for x in lines)
            tax=sum(((Decimal(str(x['Quantity']))*Decimal(str(x['UnitAmount']))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)*Decimal('.20')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) for x in lines)
            bill=dict(body['Invoices'][0],InvoiceID='bill-id' if not self.invoices else 'bill-id-'+str(len(self.invoices)+1),SubTotal=float(net),TotalTax=float(tax),Total=float(net+tax))
            self.invoices.append(bill)
            if self.uncertain_create:raise ConnectionError('Lost response')
            return {'Invoices':[bill]}
        if path.startswith('/Invoices/bill-id'):return {'Invoices':[b for b in self.invoices if b['InvoiceID']==path.rsplit('/',1)[1]]}
        raise AssertionError(path)


def test_payload_fixed_accounting_mapping():
    inv=json.loads(FIXTURE.read_text());p=build_payload(inv,'contact-id')
    assert p['Type']=='ACCPAY' and p['Status']=='DRAFT' and p['LineAmountTypes']=='Exclusive'
    assert p['LineItems'][0]['AccountCode']=='325' and p['LineItems'][0]['TaxType']=='INPUT2'
    assert p['InvoiceNumber']=='AP-2026-1001'

@pytest.mark.parametrize('field,value',[('displayed_total','95.17'),('currency','USD'),('invoice_number',None),('due_date','2026-01-01')])
def test_invalid_fixture_rejected(field,value):
    inv=json.loads(FIXTURE.read_text());inv[field]=value
    with pytest.raises(ConnectionError):validate(inv)

def test_create_retrieve_verify_then_reuse(tmp_path):
    c=FakeConnection(tmp_path);b=DemoBills(c)
    first=asyncio.run(b.create());second=asyncio.run(b.create())
    assert first['status']=='verified' and not first['reused_existing']
    assert second['reused_existing'] and second['invoice_id']==first['invoice_id']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1
    from app.ledger import Ledger
    assert Ledger(tmp_path/'ledger').invoice(first['ledger_invoice_id'])['state']=='verified'

def test_inactive_mapping_stops_before_writes(tmp_path):
    c=FakeConnection(tmp_path);c.account='ARCHIVED'
    assert asyncio.run(DemoBills(c).create())['status']=='held'
    assert not any(x[0]=='POST' for x in c.calls)

def test_uncertain_creation_with_no_match_holds(tmp_path):
    c=FakeConnection(tmp_path);c.fail_create=True;b=DemoBills(c)
    assert asyncio.run(b.create())['status']=='uncertain'
    c.fail_create=False
    assert 'needs reconciliation' in asyncio.run(b.create())['message']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_lost_response_reconciles_existing_without_recreate(tmp_path):
    c=FakeConnection(tmp_path);c.uncertain_create=True;b=DemoBills(c)
    assert asyncio.run(b.create())['status']=='uncertain'
    result=asyncio.run(b.create());assert result['reused_existing']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_conflicting_existing_bill_held(tmp_path):
    c=FakeConnection(tmp_path);b=DemoBills(c);asyncio.run(b.create());c.invoices[0]['Total']=100
    assert 'Total' in asyncio.run(b.create())['message']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_name_match_without_seed_identity_held(tmp_path):
    c=FakeConnection(tmp_path);c.contacts=[{'Name':'Alder Packing Supplies Ltd','ContactID':'wrong','ContactStatus':'ACTIVE'}]
    assert 'fixture mapping' in asyncio.run(DemoBills(c).create())['message']

def test_xero_date_format_and_line_mismatch():
    p=build_payload(json.loads(FIXTURE.read_text()),'contact-id');bill=copy.deepcopy(p)
    bill['Date']='/Date(1785715200000+0000)/'
    assert 'Date' not in bill_matches(bill,p)
    bill['LineItems'][0]['TaxType']='OUTPUT2'
    assert 'LineItems.TaxType' in bill_matches(bill,p)

def test_create_route_requires_local_preview():
    from app.main import create_app,Settings
    from fastapi.testclient import TestClient
    c=TestClient(create_app(Settings('id','secret')))
    assert c.post('/demo/bill').status_code==403
    assert c.post('/demo/bill',headers={'Origin':'http://localhost:8080'},data={'csrf':'bad'}).status_code==403

def test_concurrent_create_reuses_single_bill(tmp_path):
    c=FakeConnection(tmp_path);b=DemoBills(c)
    async def run():return await asyncio.gather(b.create(),b.create())
    results=asyncio.run(run())
    assert results[0]['invoice_id']==results[1]['invoice_id']
    assert len([x for x in c.calls if x[0]=='POST' and x[1]=='/Invoices'])==1

def test_overview_and_fixed_source_are_read_only():
    from fastapi.testclient import TestClient
    from app.main import create_app,Settings
    c=TestClient(create_app(Settings('','')))
    r=c.get('/overview')
    assert r.status_code==200 and 'AP-2026-1001' in r.text and '£95.16' in r.text
    assert c.get('/phase-overview.css').headers['content-type'].startswith('text/css')
    assert c.get('/demo/source').content.startswith(b'%PDF')
