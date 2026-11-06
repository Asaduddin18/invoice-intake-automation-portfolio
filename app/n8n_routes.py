"""Small machine interface over existing invoice services. No document payloads."""
from collections import Counter
import json
import os
import re
import secrets
import sqlite3
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from app.connection import ConnectionError
from app.mail_store import MailBusyError
from app.monthly_report import reporting_period

PREFIX='/integrations/n8n'
MONTH=re.compile(r'[0-9]{4}-(0[1-9]|1[0-2])')
STATUSES={'verified','held','awaiting_approval','awaiting_review','ready','unsupported',
          'needs_inspection','received','extracted','extracting','uncertain','processing'}


def summary(intake,report,tenant,mailbox):
    """Mirror report classification while excluding all invoice and provider fields."""
    counts=Counter();months=set();duplicates=0
    for row in intake.store.all(tenant,mailbox):
        case=intake.review.get(tenant,row['run_id']) if row['run_id'] else None
        status=row['status'];candidate=(case or {}).get('candidate') or {}
        if case:
            decision,_=intake.review.decision(tenant,case)
            outcome=(case.get('outcome') or {}).get('status')
            status=outcome if outcome in ('verified','uncertain','processing') else decision
        status='held' if status.startswith('held') else status
        counts[status if status in STATUSES else 'other']+=1
        duplicates+=bool(row['duplicate_of'])
        months.add(reporting_period(candidate.get('invoice_date'),row['received'])[0])
    reports=[]
    with report.store.db() as db:
        rows=db.execute('SELECT month,status FROM reports WHERE tenant=? AND mailbox=? ORDER BY month',(tenant,mailbox))
        for row in rows:
            if MONTH.fullmatch(row['month']):
                reports.append({'month':row['month'],'status':row['status'] if row['status'] in ('creating','pending','verified') else 'other'})
    mode=intake.review.policy(tenant)['mode']
    return {'deliveries':sum(counts.values()),'counts':dict(sorted(counts.items())),
        'duplicate_deliveries':duplicates,'available_report_months':sorted(months),
        'reports':reports,'review_policy':mode if mode in ('automatic','required') else 'unknown',
        'new_extraction_budget':0}


def register(app,connection,settings):
    token=os.getenv('INVOICE_N8N_API_TOKEN','').strip()

    def reply(request,status,code,data=None,http=200):
        return JSONResponse({'status':status,'code':code,'correlation_id':request.state.n8n_correlation,
            'data':data or {}},status_code=http,headers={'Cache-Control':'no-store'})

    def authenticate(request):
        request.state.n8n_correlation=uuid.uuid4().hex
        if not 32<=len(token)<=256:
            return reply(request,'unavailable','API_NOT_CONFIGURED',http=503)
        raw=request.headers.get('authorization','')
        if len(raw)>512 or not raw.startswith('Bearer ') or not secrets.compare_digest(raw[7:].encode(),token.encode()):
            response=reply(request,'unauthorized','UNAUTHORIZED',http=401)
            response.headers['WWW-Authenticate']='Bearer'
            return response
        if request.headers.get('origin') is not None or request.headers.get('sec-fetch-site')=='cross-site':
            return reply(request,'forbidden','SERVER_REQUEST_REQUIRED',http=403)

    def readiness():
        google=app.state.google
        state={'xero_saved':False,'google_saved':False,
            'xero_configured':bool(settings.client_id and settings.client_secret),
            'google_configured':bool(google.client_id and google.client_secret and google.mailbox),
            'remote_access_checked':False}
        tenant=None
        try:
            saved=connection.store.load()
            if saved.get('tenant_name','Demo Company (UK)')!='Demo Company (UK)' or not saved.get('tenant_id'):
                raise ConnectionError('Unsupported local connection.')
            tenant=saved['tenant_id'];state['xero_saved']=True
        except ConnectionError:pass
        try:state['google_saved']=google.store.load()['mailbox']==google.mailbox
        except ConnectionError:pass
        return state,tenant

    async def body(request,keys):
        if request.headers.get('content-type','').split(';',1)[0].strip()!='application/json':
            raise ValueError('JSON required')
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>1024:raise ValueError('Body too large')
            raw.extend(chunk)
        data=json.loads(raw)
        if type(data) is not dict or set(data)!=keys:raise ValueError('Unexpected input')
        return data

    async def operation(request,kind):
        denied=authenticate(request)
        if denied is not None:return denied
        try:
            data=await body(request,set() if kind=='intake' else {'month'})
            if kind=='report' and (type(data['month']) is not str or not MONTH.fullmatch(data['month'])):
                raise ValueError('Invalid month')
        except (ValueError,UnicodeError):
            return reply(request,'invalid','INVALID_REQUEST',http=400)
        try:
            state,tenant=readiness()
            if not all(state[k] for k in ('xero_saved','xero_configured','google_saved','google_configured')):
                return reply(request,'action_required','CONNECTION_REQUIRED',{'next_action':'inspect_local_connections'},409)
            intake=app.state.mail_intake;report=app.state.monthly_report
            if kind=='intake':
                result=await intake.poll(max_new_extractions=0)
                safe=summary(intake,report,tenant,app.state.google.mailbox)
                # Do not forward upstream or future service fields into n8n.
                safe['poll']={k:result[k] for k in ('new_deliveries','already_recorded','more_pages')}
                return reply(request,'completed','INTAKE_COMPLETED',safe)
            known=summary(intake,report,tenant,app.state.google.mailbox)['available_report_months']
            if data['month'] not in known:
                return reply(request,'invalid','UNKNOWN_REPORT_MONTH',http=400)
            result=await report.refresh(data['month'])
            return reply(request,'completed','REPORT_VERIFIED',
                {'month':result['month'],'deliveries':result['deliveries'],'report_status':result['status']})
        except MailBusyError:
            return reply(request,'busy','OPERATION_BUSY',{'next_action':'inspect_then_retry_manually'},409)
        except (ConnectionError,OSError,sqlite3.Error,ValueError,KeyError,TypeError,RuntimeError):
            # A timeout may follow a saved accounting/reporting action. Never echo raw errors.
            return reply(request,'action_required','OPERATION_FAILED',{'next_action':'inspect_saved_state','automatic_retry':False},503)

    @app.get(PREFIX+'/status')
    async def status(request:Request):
        denied=authenticate(request)
        if denied is not None:return denied
        try:
            state,tenant=readiness()
            data=summary(app.state.mail_intake,app.state.monthly_report,tenant,app.state.google.mailbox) if tenant else {
                'deliveries':0,'counts':{},'duplicate_deliveries':0,'available_report_months':[],
                'reports':[],'review_policy':None,'new_extraction_budget':0}
            data['readiness']=state
            return reply(request,'ok','STATUS_READY',data)
        except (ConnectionError,OSError,sqlite3.Error,ValueError,KeyError,TypeError):
            return reply(request,'action_required','STATUS_UNAVAILABLE',{'next_action':'inspect_saved_state'},503)

    @app.post(PREFIX+'/intake')
    async def intake(request:Request):return await operation(request,'intake')

    @app.post(PREFIX+'/report')
    async def report(request:Request):return await operation(request,'report')
