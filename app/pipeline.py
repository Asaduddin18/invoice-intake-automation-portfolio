"""Durable accounting checks for structured invoices, including extracted candidates."""
import hashlib
import json
import uuid
from pathlib import Path

from app.bills import DemoBills, CONTACT_NUMBER, build_payload, validate, bill_matches, amount, D
from app.connection import ConnectionError
from app.ledger import Ledger
from app.review import ReviewStore, ReviewError

SUPPLIER_NAME='Alder Packing Supplies Ltd'
SUPPLIER_KEY='alder-packing'

class InvoicePipeline:
    def __init__(self,connection):
        self.connection=connection
        self.ledger=Ledger(connection.store.directory/'ledger')
        self.review=ReviewStore(connection.store.directory)

    def tenant(self):
        saved=self.connection.store.load()
        if saved.get('tenant_name','Demo Company (UK)')!='Demo Company (UK)':
            raise ConnectionError('Only Demo Company (UK) is supported.')
        return saved['tenant_id']

    async def submit(self,invoice,content,filename,source_key=None,authorization=None):
        tenant=self.tenant();error=None;mapping=None
        try:
            if not content.startswith(b'%PDF') or len(content)>10_000_000:
                raise ConnectionError('Expected a PDF under 10 MB.')
            mapping=self.review.mapping(tenant,(invoice.get('supplier') or {}).get('name'))
            if not mapping:
                raise ConnectionError('Unknown supplier mapping; no automatic contact or bill creation.')
            validate(invoice, tax_rate=mapping['tax_rate'])
        except (ConnectionError,ValueError,TypeError,KeyError) as exc:
            error=str(exc) if isinstance(exc,ConnectionError) else 'Invalid confirmed invoice data.'
        if not error:
            try:
                self.review.authorize(tenant,invoice,hashlib.sha256(content).hexdigest(),authorization)
            except ReviewError as exc:
                return {'status':'awaiting_review','message':str(exc)}
        receipt=self.ledger.receive(tenant,source_key or 'manual:'+str(uuid.uuid4()),Path(filename).name,content,invoice,mapping['supplier_key'] if mapping else SUPPLIER_KEY,error)
        if receipt['outcome'].startswith('held'):
            return {'status':receipt['outcome'],'receipt_id':receipt['id'],'invoice_id':receipt['invoice_id'],'message':receipt['message']}
        result=await self.process(receipt['invoice_id'],authorization=authorization,source_sha256=receipt['sha256'])
        return {**result,'receipt_id':receipt['id'],'receipt_outcome':receipt['outcome'],'sha256':receipt['sha256']}

    async def supplier(self,tenant,mapping):
        supplier_name=mapping['supplier'];supplier_key=mapping['supplier_key']
        contact_number=mapping['contact_number']
        with self.ledger.lock('supplier:'+tenant+supplier_key) as acquired:
            if not acquired:raise ConnectionError('Supplier mapping is being processed; retry later.')
            with self.ledger.db() as db:
                row=db.execute('SELECT * FROM suppliers WHERE tenant=? AND supplier_key=?',(tenant,supplier_key)).fetchone()
            contacts=(await self.connection.request('GET','/Contacts',params={'where':'Name=="'+supplier_name+'"','includeArchived':'true'})).get('Contacts',[])
            matches=[c for c in contacts if c.get('Name')==supplier_name]
            if len(matches)>1:raise ConnectionError('Supplier name is ambiguous; resolve in Xero.')
            if matches:
                contact=matches[0]
                if contact.get('ContactStatus')!='ACTIVE':raise ConnectionError('Supplier contact is archived.')
                if contact.get('ContactNumber')!=contact_number:raise ConnectionError('Supplier exists without the fixture mapping. Confirm identity.')
                if row and row['contact_id'] and row['contact_id']!=contact['ContactID']:
                    raise ConnectionError('Supplier ContactID changed. Confirm mapping before proceeding.')
            else:
                if row:raise ConnectionError('Supplier creation/mapping is uncertain and no contact was found. Inspect Xero before retrying.')
                # Recognize unresolved legacy Phase 2 supplier intent as a hold, too.
                legacy=self.legacy(tenant)
                if legacy and legacy.get('stage')=='supplier_creation_uncertain':
                    raise ConnectionError('Legacy supplier creation is uncertain. Inspect Xero.')
                key=hashlib.sha256(('supplier:'+tenant+supplier_key).encode()).hexdigest()
                with self.ledger.db() as db:
                    db.execute('INSERT INTO suppliers VALUES(?,?,?,?,?)',(tenant,supplier_key,'uncertain',None,key))
                response=await self.connection.request('POST','/Contacts',body={'Contacts':[{'Name':supplier_name,'ContactNumber':contact_number,'EmailAddress':mapping['email']}]},idempotency_key=key)
                returned=response.get('Contacts',[])
                if len(returned)!=1 or not returned[0].get('ContactID') or returned[0].get('HasValidationErrors'):
                    raise ConnectionError('Supplier creation is uncertain. Inspect Xero.')
                contact=returned[0]
            with self.ledger.db() as db:
                db.execute('INSERT INTO suppliers VALUES(?,?,?,?,?) ON CONFLICT(tenant,supplier_key) DO UPDATE SET state=excluded.state,contact_id=excluded.contact_id',(tenant,supplier_key,'confirmed',contact['ContactID'],hashlib.sha256(('supplier:'+tenant+supplier_key).encode()).hexdigest()))
            return contact['ContactID']

    def legacy(self,tenant):
        path=self.connection.store.directory/('first-bill-'+hashlib.sha256(tenant.encode()).hexdigest()[:16]+'.json')
        if path.exists():
            try:return json.loads(path.read_text())
            except (ValueError,OSError):raise ConnectionError('Legacy receipt cannot be read. Inspect before creating another bill.') from None
        return None

    async def candidates(self,contact_id,number):
        found=[]
        # Search is paginated; do not infer absence if our bound is exhausted.
        for page in range(1,11):
            response=await self.connection.request('GET','/Invoices',params={'ContactIDs':contact_id,'InvoiceNumbers':number,'page':page,'pageSize':100,'Statuses':'DRAFT,SUBMITTED,AUTHORISED,PAID,VOIDED,DELETED'})
            batch=response.get('Invoices')
            if not isinstance(batch,list):raise ConnectionError('Could not reconcile existing bills.')
            found.extend(b for b in batch if b.get('Type')=='ACCPAY' and b.get('Contact',{}).get('ContactID')==contact_id and str(b.get('InvoiceNumber','')).strip().casefold()==number.strip().casefold())
            if len(batch)<100:return found
        raise ConnectionError('Bill reconciliation exceeded its page limit. Investigate; no creation attempted.')

    async def verify(self,row,invoice,payload,xero_id,reused):
        self.ledger.state(row['id'],'verifying','Retrieving Xero bill for comparison.',xero_id=xero_id)
        response=await self.connection.request('GET','/Invoices/'+xero_id)
        bills=response.get('Invoices',[])
        if len(bills)!=1:raise ConnectionError('Could not retrieve the saved bill. No recreation permitted.')
        bill=bills[0]
        errors=bill_matches(bill,payload)
        for key,expected in [('SubTotal',invoice['subtotal']),('TotalTax',invoice['tax']),('Total',invoice['displayed_total'])]:
            if amount(bill.get(key,'NaN'))!=D(expected):errors.append(key)
        if errors:raise ConnectionError('Existing bill conflicts with expected values: '+', '.join(errors)+'. Inspect Xero.')
        self.ledger.attempt_found(row['id'],xero_id)
        self.ledger.state(row['id'],'verified','Retrieved fields and totals match the confirmed invoice.',xero_id=xero_id)
        return {'status':'verified','ledger_invoice_id':row['id'],'reused_existing':reused,'invoice_id':xero_id,'invoice_number':invoice['invoice_number'],'supplier':invoice['supplier']['name'],'currency':'GBP','subtotal':invoice['subtotal'],'tax':invoice['tax'],'total':invoice['displayed_total'],'xero_status':'DRAFT'}

    async def process(self,invoice_id,authorization=None,source_sha256=None):
        tenant=self.tenant()
        row=self.ledger.invoice(invoice_id)
        if not row or row['tenant']!=tenant:raise ConnectionError('Invoice does not belong to the connected company.')
        with self.ledger.lock('invoice:'+invoice_id) as acquired:
            if not acquired:return {'status':'processing','ledger_invoice_id':invoice_id,'message':'Another request owns this invoice. Inspect the ledger shortly.'}
            row=self.ledger.invoice(invoice_id)
            invoice=json.loads(row['expected_json'])
            try:
                with self.review.gate():
                    self.review.authorize(tenant,invoice,source_sha256,authorization)
                    return await self._process_locked(row,invoice,tenant)
            except ReviewError as exc:
                return {'status':'awaiting_review','ledger_invoice_id':invoice_id,'message':str(exc)}

    async def _process_locked(self,row,invoice,tenant):
        invoice_id=row['id']
        try:
            mapping=self.review.mapping(tenant,invoice['supplier']['name'])
            if not mapping:raise ConnectionError('Supplier mapping is not configured.')
            checker=DemoBills(self.connection,mapping);checker.invoice=invoice
            await checker.preview()
            contact_id=await self.supplier(tenant,mapping)
            payload=build_payload(invoice,contact_id,mapping)
            self.ledger.state(invoice_id,'reconciling','Checking Xero before any creation.',contact_id=contact_id)
            if row['xero_id']:
                return await self.verify(row,invoice,payload,row['xero_id'],True)
            matches=await self.candidates(contact_id,invoice['invoice_number'])
            if len(matches)>1:raise ConnectionError('Multiple matching bills exist. Investigate before proceeding.')
            if matches:
                return await self.verify(row,invoice,payload,matches[0]['InvoiceID'],True)
            if self.ledger.attempt(invoice_id):
                raise ConnectionError('Earlier bill operation needs reconciliation. No matching bill found; no blind recreation.')
            legacy=self.legacy(tenant)
            if invoice['invoice_number']=='AP-2026-1001' and legacy and legacy.get('stage') in ('bill_creation_uncertain','verification_failed','verified'):
                raise ConnectionError('Legacy Phase 2 operation needs reconciliation. Inspect Xero before another create.')
            key=self.ledger.intent(invoice_id,payload)
            response=await self.connection.request('POST','/Invoices',body={'Invoices':[payload]},idempotency_key=key)
            created=response.get('Invoices',[])
            if len(created)!=1 or not created[0].get('InvoiceID') or created[0].get('HasErrors'):
                raise ConnectionError('Creation did not return a verified identifier. Reconcile before retrying.')
            xero_id=created[0]['InvoiceID']
            # Persist identity before retrieval, so a failed verification cannot lose it.
            self.ledger.attempt_found(invoice_id,xero_id)
            self.ledger.state(invoice_id,'verifying','Xero returned an identifier; verification pending.',xero_id=xero_id)
            return await self.verify(row,invoice,payload,xero_id,False)
        except (ConnectionError,ValueError,TypeError,KeyError,ArithmeticError) as exc:
            message=str(exc) if isinstance(exc,ConnectionError) else 'Unexpected invoice or Xero data. Inspect before retrying.'
            state='uncertain' if self.ledger.attempt(invoice_id) else 'held'
            self.ledger.state(invoice_id,state,message)
            return {'status':state,'ledger_invoice_id':invoice_id,'message':message}
