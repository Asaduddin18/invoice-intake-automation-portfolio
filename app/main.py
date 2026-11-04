"""Local synthetic invoice demo: protected OAuth, extraction and draft-only accounting."""
import os
import html
import json
from pathlib import Path
import secrets
import time
import sqlite3
import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from app.bills import DemoBills
from app.pipeline import InvoicePipeline
from app.ledger_view import render as render_ledger
from app.samples import sample
from app.extraction import ExtractionError
from app.extraction_runs import ExtractionRuns
from app.extraction_view import render as render_extractions
from app.connection import TokenStore, XeroConnection, ConnectionError
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

load_dotenv()
AUTHORIZE_URL = 'https://login.xero.com/identity/connect/authorize'
TOKEN_URL = 'https://identity.xero.com/connect/token'
CONNECTIONS_URL = 'https://api.xero.com/connections'
COOKIE = 'xero_oauth_state'
XERO_SCOPES = 'accounting.settings.read accounting.contacts accounting.invoices offline_access'

@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str = 'http://localhost:8080/oauth/xero/callback'
    storage_dir: str = str(Path(__file__).resolve().parents[1] / '.runtime')


def create_app(settings=None, transport=None):
    settings = settings or Settings(os.getenv('XERO_CLIENT_ID', ''), os.getenv('XERO_CLIENT_SECRET', ''), os.getenv('XERO_REDIRECT_URI', 'http://localhost:8080/oauth/xero/callback'))
    connection = XeroConnection(TokenStore(settings.storage_dir), settings, transport)
    bills = DemoBills(connection)
    app = FastAPI(title='Invoice Intake: Xero connection test', docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
    # One process, ten-minute authorization attempts. Restarting clears pending flows.
    pending = {}
    app.state.pending = pending
    extraction_lock = asyncio.Lock()
    extraction_choices = ('invoice_001', 'invoice_002', 'invoice_003', 'invoice_009', 'invoice_013', 'invoice_015')

    @app.middleware('http')
    async def privacy_headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self' https://login.xero.com; frame-ancestors 'none'"
        return response

    @app.get('/', response_class=HTMLResponse)
    async def home():
        return '<h1>Xero connection test</h1><p>Connect only Demo Company (UK). This milestone reads company names, saves tokens locally and refreshes access. Draft supplier bills can be created only through the explicit demo preview.</p><a href="/oauth/xero/start">Connect to Xero</a><p><a href="/xero/company">Check saved connection</a></p><p><a href="/demo/bill">Preview sample draft bill</a></p><p><a href="/overview">Phase 2 overview and learning guide</a></p><p><a href="/ledger">Phase 3 invoice ledger</a></p><p><a href="/extract">Phase 4 PDF extraction</a></p><p><a href="/review">Phase 5 invoice workflow</a></p><p><a href="/mail">Phase 6 Gmail intake and reporting</a></p>'

    @app.get('/oauth/xero/start')
    @app.post('/oauth/xero/start')
    async def start(request: Request):
        # Permit direct or same-origin navigation; reject cross-site browser requests.
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Start from the local app home page.'}, status_code=403)
        if request.headers.get('origin') not in (None, 'http://localhost:8080', 'http://127.0.0.1:8080'):
            return JSONResponse({'error': 'Untrusted origin.'}, status_code=403)
        if not settings.client_id or not settings.client_secret:
            return JSONResponse({'error': 'Add XERO_CLIENT_ID and XERO_CLIENT_SECRET to your local .env, then restart.'}, status_code=503)
        if settings.redirect_uri != 'http://localhost:8080/oauth/xero/callback':
            return JSONResponse({'error': 'This local scaffold expects the registered localhost:8080 callback.'}, status_code=503)
        now = time.monotonic()
        for key in list(pending):
            if pending[key] <= now:
                pending.pop(key)
        if len(pending) >= 100:
            return JSONResponse({'error': 'Too many pending attempts. Restart the local server.'}, status_code=429)
        state = secrets.token_urlsafe(32)
        pending[state] = now + 600
        # Phase 2: settings read, contacts/invoices write and offline refresh.
        query = urlencode({'response_type': 'code', 'client_id': settings.client_id, 'redirect_uri': settings.redirect_uri, 'scope': XERO_SCOPES, 'state': state})
        response = RedirectResponse(AUTHORIZE_URL + '?' + query, status_code=303)
        response.set_cookie(COOKIE, state, max_age=600, httponly=True, samesite='lax', path='/oauth/xero')
        return response

    @app.get('/oauth/xero/callback')
    async def callback(request: Request):
        state = request.query_params.get('state', '')
        cookie = request.cookies.get(COOKIE, '')
        expires = pending.get(state, 0)
        valid = bool(state and cookie and secrets.compare_digest(state, cookie) and expires > time.monotonic())
        if not valid:
            return JSONResponse({'error': 'Invalid, expired or already-used authorization attempt. Start again from localhost:8080.'}, status_code=400)
        pending.pop(state)
        response = RedirectResponse('/oauth/xero/result', status_code=303)
        response.delete_cookie(COOKIE, path='/oauth/xero')
        # Result is browser-bound, short-lived, and contains only sanitized company names.
        result_id = secrets.token_urlsafe(32)
        result = {'status': 'error', 'message': 'Xero authorization did not complete.'}
        if request.query_params.get('error'):
            result['message'] = 'Authorization was declined. You can start again.'
        elif not request.query_params.get('code'):
            result['message'] = 'Xero did not return an authorization code.'
        else:
            try:
                async with httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False) as client:
                    token_response = await client.post(TOKEN_URL, auth=(settings.client_id, settings.client_secret), data={'grant_type': 'authorization_code', 'code': request.query_params['code'], 'redirect_uri': settings.redirect_uri})
                    token_response.raise_for_status()
                    tokens = token_response.json()
                    access_token = tokens['access_token']
                    conn = await client.get(CONNECTIONS_URL, headers={'Authorization': 'Bearer ' + access_token})
                    conn.raise_for_status()
                    companies = conn.json()
                    if not isinstance(companies, list):
                        raise ValueError('Unexpected connections format')
                    names = [item['tenantName'] for item in companies if item.get('tenantType') == 'ORGANISATION' and isinstance(item.get('tenantName'), str)]
                    await connection.save_authorization(tokens, companies)
                    result = {'status': 'connected' if names else 'no_organisations', 'companies': names, 'demo_company_uk_connected': 'Demo Company (UK)' in names, 'message': 'Connection verified. Tokens saved locally. No bill is created by connecting.'}
            except ConnectionError as exc:
                result['message'] = str(exc)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
                # Never include upstream bodies, codes, secrets or tokens in responses/logs.
                result['message'] = 'Could not verify the connection. Check credentials and registered callback, then start again. Upstream details are intentionally hidden.'
        now = time.monotonic()
        for key in list(results):
            if results[key][0] <= now:
                results.pop(key)
        results[result_id] = (now + 600, result)
        response.set_cookie('xero_result', result_id, max_age=600, httponly=True, samesite='lax', path='/oauth/xero/result')
        return response

    results = {}

    @app.get('/oauth/xero/result')
    async def get_result(request: Request):
        record = results.get(request.cookies.get('xero_result', ''))
        if not record or record[0] <= time.monotonic():
            return JSONResponse({'error': 'No current result. Start a new connection test.'}, status_code=400)
        response = JSONResponse(record[1])
        return response

    @app.get('/xero/company')
    async def company(request: Request):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Open the local home page first.'}, status_code=403)
        try:
            return await connection.company()
        except ConnectionError as exc:
            return JSONResponse({'status': 'reconnect_or_retry', 'message': str(exc)}, status_code=409)

    @app.get('/demo/bill', response_class=HTMLResponse)
    async def preview_bill(request: Request):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Open the local home page first.'}, status_code=403)
        try:
            preview = await bills.preview()
        except ConnectionError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        csrf = secrets.token_urlsafe(32)
        content = '<h1>Preview synthetic supplier bill</h1><p>Target: Demo Company (UK). Account 325 Direct Expenses; INPUT2 20% VAT on Expenses. Creates a draft only, with no payment or authorization.</p><pre>' + html.escape(json.dumps(preview, indent=2)) + '</pre><form method="post" action="/demo/bill"><input type="hidden" name="csrf" value="' + csrf + '"><button>Create or verify sample draft</button></form>'
        response = HTMLResponse(content)
        response.set_cookie('bill_csrf', csrf, max_age=600, httponly=True, samesite='lax', path='/demo/bill')
        return response

    @app.post('/demo/bill')
    async def create_bill(request: Request):
        from urllib.parse import parse_qs
        if request.headers.get('origin') != 'http://localhost:8080' or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Submit from the local preview page.'}, status_code=403)
        raw = await request.body()
        if len(raw) > 1024:
            return JSONResponse({'error': 'Invalid submission.'}, status_code=400)
        token = parse_qs(raw.decode(errors='replace')).get('csrf', [''])[0]
        cookie = request.cookies.get('bill_csrf', '')
        if not token or not cookie or not secrets.compare_digest(token, cookie):
            return JSONResponse({'error': 'Preview the bill before submitting.'}, status_code=403)
        try:
            return await bills.create()
        except (ConnectionError, ValueError, OSError) as exc:
            message = str(exc) if isinstance(exc, ConnectionError) else 'Local bill receipt could not be read or saved. Inspect Xero before retrying.'
            return JSONResponse({'status': 'held', 'message': message}, status_code=409)

    @app.get('/overview')
    async def overview():
        return FileResponse(Path(__file__).parent / 'pages' / 'phase02.html', media_type='text/html')

    @app.get('/phase-overview.css')
    async def overview_css():
        return FileResponse(Path(__file__).parent / 'pages' / 'phase02.css', media_type='text/css')

    @app.get('/demo/source')
    async def source_pdf():
        return FileResponse(Path(__file__).resolve().parents[1] / 'fixtures' / 'invoice_001.pdf', media_type='application/pdf', filename='invoice_001.pdf', content_disposition_type='inline')

    @app.get('/ledger', response_class=HTMLResponse)
    async def ledger_page(request: Request):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Navigate from the local home page.'}, status_code=403)
        try:
            pipeline=InvoicePipeline(connection)
            snapshot=pipeline.ledger.snapshot(pipeline.tenant())
        except (ConnectionError,ValueError,sqlite3.Error,OSError):
            return JSONResponse({'error': 'Connect the UK demo and check local storage before opening the ledger.'},status_code=409)
        csrf=secrets.token_urlsafe(32)
        response=HTMLResponse(render_ledger(snapshot,csrf))
        response.set_cookie('ledger_csrf',csrf,max_age=600,httponly=True,samesite='lax',path='/ledger')
        return response

    @app.post('/ledger/submit')
    async def submit_sample(request: Request):
        from urllib.parse import parse_qs
        if request.headers.get('origin') != 'http://localhost:8080' or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Submit from the local ledger.'},status_code=403)
        raw=await request.body()
        if len(raw)>1024:return JSONResponse({'error':'Invalid submission.'},status_code=400)
        form=parse_qs(raw.decode(errors='replace'))
        token=form.get('csrf',[''])[0];cookie=request.cookies.get('ledger_csrf','')
        if not token or not cookie or not secrets.compare_digest(token,cookie):
            return JSONResponse({'error':'Reload the ledger before submitting.'},status_code=403)
        try:
            invoice,content,filename=sample(form.get('sample',[''])[0])
            result=await InvoicePipeline(connection).submit(invoice,content,filename)
            if result.get('status')=='awaiting_review':
                return JSONResponse(result,status_code=409)
            return RedirectResponse('/ledger',status_code=303)
        except (ConnectionError,ValueError,sqlite3.Error,OSError):
            return JSONResponse({'error':'Submission could not finish. Inspect the ledger and Xero before retrying writes.'},status_code=409)

    @app.get('/ledger/data')
    async def ledger_data(request: Request):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error':'Navigate from the local home page.'},status_code=403)
        try:
            pipeline=InvoicePipeline(connection)
            return pipeline.ledger.snapshot(pipeline.tenant())
        except (ConnectionError,ValueError,sqlite3.Error,OSError):
            return JSONResponse({'error':'Ledger unavailable. Check connection and storage.'},status_code=409)

    @app.get('/extract', response_class=HTMLResponse)
    async def extraction_page(request: Request):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Open the local home page first.'}, status_code=403)
        csrf = secrets.token_urlsafe(32)
        runs = ExtractionRuns(settings.storage_dir).recent()
        response = HTMLResponse(render_extractions(runs, csrf, extraction_choices))
        response.set_cookie('extraction_csrf', csrf, max_age=600, httponly=True, samesite='lax', path='/extract')
        return response

    async def extraction_form(request):
        from urllib.parse import parse_qs
        if request.headers.get('origin') != 'http://localhost:8080' or request.headers.get('sec-fetch-site') == 'cross-site':
            raise ValueError('Submit from the local extraction page.')
        raw = await request.body()
        if len(raw) > 1024: raise ValueError('Invalid submission.')
        form = parse_qs(raw.decode(errors='replace'))
        token = form.get('csrf', [''])[0]
        cookie = request.cookies.get('extraction_csrf', '')
        if not token or not cookie or not secrets.compare_digest(token, cookie):
            raise ValueError('Reload the extraction page before submitting.')
        return form

    @app.post('/extract/run')
    async def extract_sample(request: Request):
        try:
            form = await extraction_form(request)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=403)
        name = form.get('sample', [''])[0]
        if name not in extraction_choices:
            return JSONResponse({'error': 'Unknown synthetic sample.'}, status_code=400)
        if extraction_lock.locked():
            return JSONResponse({'error': 'Extraction is running. Wait and refresh; do not resubmit.'}, status_code=409)
        async with extraction_lock:
            store = ExtractionRuns(settings.storage_dir)
            data = (Path(__file__).resolve().parents[1] / 'fixtures/extraction/tuning' / (name+'.pdf')).read_bytes()
            # Reopening a result or repeating a sample does not incur another paid call.
            import hashlib
            digest = hashlib.sha256(data).hexdigest()
            from app.extraction import PROMPT, PROMPT_VERSION
            prompt_hash = hashlib.sha256(PROMPT.read_bytes()).hexdigest()
            model = os.getenv('DEEPSEEK_MODEL') or 'deepseek-flash'
            previous = store.cached(digest, prompt_hash, model)
            if previous is None:
                await store.run(data, name+'.pdf', os.getenv('DEEPSEEK_API_KEY', ''), model=model)
        return RedirectResponse('/extract', status_code=303)

    @app.post('/extract/submit')
    async def submit_extraction(request: Request):
        try:
            form = await extraction_form(request)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=403)
        try:
            store = ExtractionRuns(settings.storage_dir)
            run = store.load(form.get('run', [''])[0])
            if run.get('status') != 'extracted_valid':
                return JSONResponse({'error': 'Extraction is held; no accounting submission allowed.'}, status_code=409)
            from app.review import ReviewStore
            from app.workflow import submit_case
            tenant=InvoicePipeline(connection).tenant()
            case=ReviewStore(settings.storage_dir).ensure_case(tenant,run['id'])
            result=await submit_case(connection,case['id'],case['revision'])
            run['downstream_result'] = result
            store.save(run)
            return RedirectResponse('/review/'+case['id'], status_code=303)
        except (ExtractionError, ConnectionError, ValueError, OSError, sqlite3.Error):
            return JSONResponse({'error': 'Cannot submit this extraction. Inspect its evidence and the ledger.'}, status_code=409)

    @app.get('/extract/source/{run_id}')
    async def extraction_source(run_id: str, request: Request):
        from fastapi.responses import Response
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Open the local home page first.'}, status_code=403)
        try:
            store = ExtractionRuns(settings.storage_dir)
            return Response(store.source(run_id), media_type='application/pdf')
        except (ValueError, OSError, ExtractionError):
            return JSONResponse({'error': 'Source unavailable or changed.'}, status_code=404)

    from app.review_routes import register
    register(app,connection,settings)
    from app.google_routes import register as register_google
    register_google(app,connection,settings,extraction_lock)
    return app

app = create_app()
