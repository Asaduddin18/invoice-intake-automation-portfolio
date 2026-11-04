"""Verify existing synthetic Gmail deliveries and reports without paid extraction.

Run after browser consent and test email delivery. Optional --exercise-report-retry
injects one local reporting failure, then retries against live Google Sheets.
It does not revoke account permissions or call DeepSeek.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import Settings
from app.connection import TokenStore,XeroConnection
from app.google_connection import GoogleConnection,GoogleError
from app.mail_intake import MailIntake
from app.monthly_report import MonthlyReport
from app.ledger import Ledger
import os

ROOT=Path(__file__).resolve().parents[1]

def counts(directory,tenant):
    ledger=Ledger(Path(directory)/'ledger').snapshot(tenant)
    runs=list((Path(directory)/'extractions').glob('*/result.json'))
    return {'saved_extractions':len(runs),'ledger_invoices':len(ledger['invoices']),
            'bill_creation_intents':len(ledger['attempts'])}

async def verify(args):
    settings=Settings(os.environ['XERO_CLIENT_ID'],os.environ['XERO_CLIENT_SECRET'])
    connection=XeroConnection(TokenStore(settings.storage_dir),settings)
    tenant=connection.store.load()['tenant_id']
    google=GoogleConnection(settings.storage_dir)
    intake=MailIntake(connection,google,asyncio.Lock())
    async def forbid_paid(*a,**kw):raise AssertionError('This verification never permits a new model call.')
    intake.runs.run=forbid_paid
    before=counts(settings.storage_dir,tenant)
    first=await intake.poll();second=await intake.poll()
    report=MonthlyReport(connection,google);failure=None
    if args.exercise_report_retry:
        original=google.request
        async def fail_write(method,url,**kwargs):
            if method=='PUT':raise GoogleError('Deliberate local report-write failure for recovery verification.')
            return await original(method,url,**kwargs)
        google.request=fail_write
        try:await report.refresh('2026-08')
        except GoogleError:
            failure={'injection':'local failure before Sheets PUT, not a real provider outage',
                     'checkpoint':report.store.report(tenant,google.mailbox,'2026-08')['status']}
        finally:google.request=original
    results=[]
    for month in args.month:
        results.append(await report.refresh(month))
    checked=[];seen=set()
    for delivery in intake.store.all(tenant,google.mailbox):
        if not delivery['run_id']:continue
        case=intake.review.get(tenant,delivery['run_id']);outcome=(case or {}).get('outcome') or {}
        xid=outcome.get('invoice_id') if outcome.get('status')=='verified' else None
        if not xid or xid in seen:continue
        seen.add(xid)
        response=await connection.request('GET','/Invoices/'+xid)
        bill=response['Invoices'][0]
        if bill['Status']!='DRAFT' or bill['InvoiceNumber']!=case['candidate']['invoice_number']:
            raise AssertionError('Live Xero bill did not match expected draft identity.')
        if Decimal(str(bill['Total']))!=Decimal(case['candidate']['displayed_total']):
            raise AssertionError('Live Xero total differs from saved source.')
        checked.append({'invoice_id':xid,'invoice_number':bill['InvoiceNumber'],'status':bill['Status'],'total':bill['Total'],'currency':bill['CurrencyCode']})
    after=counts(settings.storage_dir,tenant)
    if after!=before:raise AssertionError('Read/retry verification unexpectedly changed extraction or accounting counts.')
    evidence={'verified_at':datetime.now(timezone.utc).isoformat(),'live_google_connection':True,
        'first_poll':first,'repeat_poll':second,'before':before,'after':after,
        'new_model_calls':0,'new_bill_creation_intents':0,'report_recovery':failure,
        'reports':results,'xero_drafts_retrieved':checked,
        'deliveries':[{'id':r['id'],'filename':r['filename'],'status':r['status'],
            'run_id':r['run_id'],'duplicate_of':r['duplicate_of'],'source_sha256':r['source_sha256']}
            for r in intake.store.all(tenant,google.mailbox)]}
    output=ROOT/'artifacts'/'phase-06-live-evidence.json'
    output.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'new_deliveries':first['new_deliveries'],'repeated_poll_new_deliveries':second['new_deliveries'],
        'reports':results,'xero_drafts_checked':len(checked),'new_model_calls':0,'counts_unchanged':before==after},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month',action='append',default=None)
    parser.add_argument('--exercise-report-retry',action='store_true')
    args=parser.parse_args();args.month=args.month or ['2026-08','2026-11']
    asyncio.run(verify(args))
