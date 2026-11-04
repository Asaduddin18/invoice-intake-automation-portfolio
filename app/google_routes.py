"""Local Google connection, explicit bounded polling and independent report refresh."""
import base64
import hashlib
import secrets
import sqlite3
import time
from datetime import datetime,timezone
from urllib.parse import urlencode,parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from app.connection import ConnectionError
from app.google_connection import GoogleConnection,GoogleError,SCOPES,REDIRECT
from app.mail_intake import MailIntake
from app.mail_store import MailStore
from app.monthly_report import MonthlyReport
from app.pipeline import InvoicePipeline
from app.review_view import page,E,form,hidden


def register(app,connection,settings,extraction_lock,google=None):
    google=google or GoogleConnection(settings.storage_dir)
    pending={};results={}
    app.state.google=google
    intake=MailIntake(connection,google,extraction_lock)
    report=MonthlyReport(connection,google)
    app.state.mail_intake=intake;app.state.monthly_report=report

    def allowed(request):
        if request.headers.get('sec-fetch-site')=='cross-site':raise GoogleError('Open the local home page first.')

    async def fields(request):
        allowed(request)
        if request.headers.get('origin')!='http://localhost:8080':raise GoogleError('Submit from the local Gmail intake page.')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>4096:raise GoogleError('Form is too large.')
        data={k:v[0] for k,v in parse_qs(raw.decode(errors='replace')).items()}
        token=data.get('csrf','');cookie=request.cookies.get('mail_csrf','')
        if not token or not cookie or not secrets.compare_digest(token,cookie):raise GoogleError('Reload the Gmail intake page before submitting.')
        return data

    def failure(exc,status=409):
        message=str(exc) if isinstance(exc,ConnectionError) else 'Local integration state could not be read. Inspect saved receipts before retrying.'
        return HTMLResponse(page('Google action held','<h1>Action held</h1><p>'+E(message)+'</p><a href="/mail">Return to Gmail intake</a>'),status_code=status)

    @app.get('/oauth/google/start')
    async def start(request:Request):
        try:
            allowed(request);google.configured()
            if request.headers.get('origin') not in (None,'http://localhost:8080'):raise GoogleError('Start at the local home page.')
            now=time.monotonic()
            for key in list(pending):
                if pending[key][0]<now:pending.pop(key)
            if len(pending)>=50:raise GoogleError('Too many pending connections. Wait or restart.')
            state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
            pending[state]=(now+600,verifier)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            query=urlencode({'response_type':'code','client_id':google.client_id,'redirect_uri':REDIRECT,
                'scope':' '.join(SCOPES),'state':state,'access_type':'offline','prompt':'consent',
                'login_hint':google.mailbox,'code_challenge':challenge,'code_challenge_method':'S256'})
            response=RedirectResponse('https://accounts.google.com/o/oauth2/v2/auth?'+query,status_code=303)
            response.set_cookie('google_oauth_state',state,httponly=True,samesite='lax',max_age=600,path='/oauth/google')
            return response
        except ConnectionError as exc:return failure(exc)

    @app.get('/oauth/google/callback')
    async def callback(request:Request):
        state=request.query_params.get('state','');cookie=request.cookies.get('google_oauth_state','')
        item=pending.get(state)
        if not state or not cookie or not secrets.compare_digest(state,cookie) or not item or item[0]<time.monotonic():
            return failure(GoogleError('Invalid, expired or reused Google authorization. Start again.'),400)
        pending.pop(state)
        result={'status':'held','message':'Google authorization was declined or did not return a code.'}
        if not request.query_params.get('error') and request.query_params.get('code'):
            try:
                mailbox=await google.authorize(request.query_params['code'],item[1])
                result={'status':'connected','mailbox':mailbox,'message':'Google account verified and encrypted offline connection saved.'}
            except (ConnectionError,ValueError,OSError,KeyError) as exc:
                result={'status':'held','message':str(exc) if isinstance(exc,ConnectionError) else 'Google connection did not complete. Start again.'}
        now=time.monotonic()
        for key in list(results):
            if results[key][0]<now:results.pop(key)
        rid=secrets.token_urlsafe(32);results[rid]=(now+600,result)
        response=RedirectResponse('/oauth/google/result',status_code=303)
        response.delete_cookie('google_oauth_state',path='/oauth/google')
        response.set_cookie('google_result',rid,httponly=True,samesite='lax',max_age=600,path='/oauth/google/result')
        return response

    @app.get('/oauth/google/result')
    async def result(request:Request):
        record=results.get(request.cookies.get('google_result',''))
        if not record or record[0]<time.monotonic():return failure(GoogleError('No current Google result. Open Gmail intake to check saved connection.'),400)
        return HTMLResponse(page('Google connection','<h1>'+E(record[1]['status'])+'</h1><p>'+E(record[1]['message'])+'</p><a href="/mail">Open Gmail intake</a>'))

    @app.get('/mail')
    async def overview(request:Request):
        try:
            allowed(request);tenant=InvoicePipeline(connection).tenant();csrf=secrets.token_urlsafe(32)
            try:saved=google.store.load();connected=saved['mailbox']==google.mailbox
            except ConnectionError:connected=False
            body='<h1>Phase 6: Gmail intake and monthly report</h1><p>Mailbox: '+E(google.mailbox)+' · Label: Invoices-Demo · '+('Connection saved' if connected else 'Connection required')+'</p>'
            body+='<p><a href="/oauth/google/start">Connect Google account</a></p><p>Gmail access is read-only across the mailbox. The label filters processing. Report access covers app-created files. Google Testing refresh tokens may expire after seven days.</p>'
            last=getattr(app.state,'last_poll',None)
            if last:
                body+='<p class="notice">Last poll: '+str(last['new_deliveries'])+' new deliveries, '+str(last['already_recorded'])+' already recorded. '+('More messages are available; poll again.' if last['more_pages'] else 'This page of the label is complete.')+'</p>'
            body+='<section class="panel"><h2>Receive labeled attachments</h2><p>One poll reads at most 10 messages. Repeated deliveries are skipped. Saved PDF extractions are reused. At most three new documents per poll can use DeepSeek, with at most one text and one vision request each. Interruptions remain visible for explicit inspection.</p>'+form('/mail/poll',csrf,'<button>Poll Invoices-Demo</button>')+'</section>'
            body+='<section class="panel"><h2>Monthly report</h2><p>Month follows invoice date. Missing or invalid invoice dates use email receipt month (UTC). Each delivery has a stable row ID; each verified Xero bill is counted once per report. This refresh never invokes accounting.</p>'+form('/mail/report',csrf,'<label>Month<input type="month" name="month" value="2026-08" required></label><button>Refresh monthly report</button>')
            with report.store.db() as db:
                reports=[dict(r) for r in db.execute('SELECT * FROM reports WHERE tenant=? AND mailbox=? ORDER BY month',(tenant,google.mailbox))]
            for r in reports:
                body+='<p>'+E(r['month'])+' · '+E(r['status'])+' · '+E(r['detail'])
                if r['spreadsheet_id']:body+=' · <a href="https://docs.google.com/spreadsheets/d/'+E(r['spreadsheet_id'],quote=True)+'/edit">Open report</a>'
                body+='</p>'
                if r['status']=='creating' and not r['spreadsheet_id']:
                    body+=form('/mail/report/adopt',csrf,hidden('month',r['month'])+'<label>Existing app report spreadsheet ID<input name="spreadsheet_id" required></label><p>Inspect Drive for the report created by this app. Its Intake tab is reserved for generated rows.</p><button>Recover existing report</button>')
            body+='</section><section class="panel"><h2>Received attachments</h2><div class="tablewrap"><table><thead><tr><th>Received UTC</th><th>File</th><th>Status</th><th>Evidence</th><th>Reason</th></tr></thead><tbody>'
            for row in reversed(intake.store.all(tenant,google.mailbox)):
                link='<a href="/review/'+row['run_id']+'">Source and workflow</a>' if row['run_id'] else ''
                if row['duplicate_of']:link+='<p>Repeated PDF content</p>'
                if row['status'] in ('received','extracted','needs_inspection','extracting'):
                    link+=form('/mail/resume',csrf,hidden('delivery',row['id'])+'<button>Inspect and resume delivery</button>')
                body+='<tr><td>'+datetime.fromtimestamp(row['received'],timezone.utc).strftime('%Y-%m-%d %H:%M')+'</td><td>'+E(row['filename'])+'</td><td>'+E(row['status'])+'</td><td>'+link+'</td><td>'+E(row['detail'])+'</td></tr>'
            body+='</tbody></table></div></section>'
            response=HTMLResponse(page('Gmail invoice intake',body));response.set_cookie('mail_csrf',csrf,httponly=True,samesite='lax',max_age=600,path='/mail')
            return response
        except (ConnectionError,ValueError,OSError,sqlite3.Error) as exc:return failure(exc)

    @app.post('/mail/poll')
    async def poll(request:Request):
        try:
            await fields(request);app.state.last_poll=await intake.poll()
            return RedirectResponse('/mail',status_code=303)
        except (ConnectionError,ValueError,OSError,sqlite3.Error) as exc:return failure(exc)

    @app.post('/mail/resume')
    async def resume(request:Request):
        try:
            data=await fields(request);await intake.resume(data.get('delivery',''))
            return RedirectResponse('/mail',status_code=303)
        except (ConnectionError,ValueError,OSError,sqlite3.Error) as exc:return failure(exc)

    @app.post('/mail/report/adopt')
    async def adopt_report(request:Request):
        try:
            data=await fields(request);await report.adopt(data.get('month',''),data.get('spreadsheet_id',''))
            return RedirectResponse('/mail',status_code=303)
        except (ConnectionError,ValueError,OSError,sqlite3.Error,KeyError) as exc:return failure(exc)

    @app.post('/mail/report')
    async def refresh(request:Request):
        try:
            data=await fields(request);await report.refresh(data.get('month',''))
            return RedirectResponse('/mail',status_code=303)
        except (ConnectionError,ValueError,OSError,sqlite3.Error,KeyError) as exc:return failure(exc)
