"""Local browser workflow, protected forms and source views."""
import secrets
import sqlite3
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.bills import DemoBills
from app.connection import ConnectionError
from app.extraction import ExtractionError, pdf_reader, render_pages
from app.extraction_runs import ExtractionRuns
from app.pipeline import InvoicePipeline
from app.review import ReviewStore, ReviewError
from app.review_view import detail, queue, page, E
from app.workflow import submit_case


def register(app, connection, settings):
    def context():
        pipeline=InvoicePipeline(connection)
        return ReviewStore(settings.storage_dir),pipeline.tenant()

    def allowed(request):
        if request.headers.get('sec-fetch-site')=='cross-site':
            raise ReviewError('Open the local home page first.')

    async def form_data(request):
        allowed(request)
        if request.headers.get('origin')!='http://localhost:8080':
            raise ReviewError('Submit from the local workflow page.')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>200000:raise ReviewError('Submission is too large.')
        data=parse_qs(body.decode(errors='replace'),keep_blank_values=True)
        token=data.get('csrf',[''])[0];cookie=request.cookies.get('review_csrf','')
        if not token or not cookie or not secrets.compare_digest(token,cookie):
            raise ReviewError('Reload the workflow page before submitting.')
        if len(data.get('reason',[''])[0])>1000:raise ReviewError('Reason exceeds 1,000 characters.')
        return {key:values[0] for key,values in data.items()}

    def html_response(content,csrf):
        response=HTMLResponse(content)
        response.set_cookie('review_csrf',csrf,max_age=600,httponly=True,samesite='lax',path='/review')
        return response

    def error(exc,status=409):
        message=str(exc) if isinstance(exc,ConnectionError) else 'Workflow data is unavailable or invalid. Reload and inspect before retrying.'
        return HTMLResponse(page('Workflow action held','<h1>Action held</h1><p>'+E(message)+'</p><a href="/review">Return to workflow</a>'),status_code=status)

    async def protected_form(request):
        try:return await form_data(request),None
        except ReviewError as exc:return None,error(exc,403)

    @app.get('/review')
    async def review_queue(request:Request):
        try:
            allowed(request);store,tenant=context();csrf=secrets.token_urlsafe(32)
            return html_response(queue(store,tenant,ExtractionRuns(settings.storage_dir).recent(),csrf),csrf)
        except (ConnectionError,ValueError,OSError,sqlite3.Error):return JSONResponse({'error':'Connect the UK demo and check workflow storage.'},status_code=409)

    @app.post('/review/policy')
    async def change_policy(request:Request):
        data,invalid=await protected_form(request)
        if invalid is not None:return invalid
        try:
            store,tenant=context();store.set_policy(tenant,data.get('mode',''),data.get('reason',''))
            return RedirectResponse('/review',status_code=303)
        except (ConnectionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.post('/review/intake')
    async def intake(request:Request):
        data,invalid=await protected_form(request)
        if invalid is not None:return invalid
        try:
            store,tenant=context();case=store.ensure_case(tenant,data.get('run',''))
            await submit_case(connection,case['id'],case['revision'])
            return RedirectResponse('/review/'+case['id'],status_code=303)
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.get('/review/{case_id}')
    async def review_case(case_id:str,request:Request):
        try:
            allowed(request);store,tenant=context();case=store.ensure_case(tenant,case_id)
            data=ExtractionRuns(settings.storage_dir).source(case_id)
            pages=len(pdf_reader(data).pages)
            number=max(1,min(pages,int(request.query_params.get('page','1'))))
            csrf=secrets.token_urlsafe(32)
            return html_response(detail(store,tenant,case,csrf,pages,number),csrf)
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.get('/review/{case_id}/source')
    async def review_source(case_id:str,request:Request):
        try:
            allowed(request);store,tenant=context()
            if not store.get(tenant,case_id):raise ReviewError('Invoice does not belong to the connected tenant.')
            return Response(ExtractionRuns(settings.storage_dir).source(case_id),media_type='application/pdf')
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.get('/review/{case_id}/page/{number}')
    async def source_page(case_id:str,number:int,request:Request):
        try:
            allowed(request);store,tenant=context()
            if not store.get(tenant,case_id):raise ReviewError('Invoice does not belong to the connected tenant.')
            images=render_pages(ExtractionRuns(settings.storage_dir).source(case_id))
            if not 1<=number<=len(images):raise ReviewError('Page is outside this document.')
            return Response(images[number-1],media_type='image/png')
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.post('/review/{case_id}/correct')
    async def correct(case_id:str,request:Request):
        data,invalid=await protected_form(request)
        if invalid is not None:return invalid
        try:
            store,tenant=context();case=store.get(tenant,case_id)
            if not case:raise ReviewError('Invoice case not found.')
            value=lambda key:data.get(key,'').strip() or None
            candidate={key:value(key) for key in ('invoice_number','invoice_date','due_date','currency','subtotal','tax','displayed_total')}
            candidate['supplier']={key:value('supplier_'+key) for key in ('name','email','address')}
            count=int(data.get('line_count','0'))
            if not 1<=count<=100:raise ReviewError('Use 1 to 100 invoice lines.')
            lines=[]
            for i in range(count):
                if data.get('remove_'+str(i))=='yes':continue
                line={key:value('line_'+str(i)+'_'+key) for key in ('description','quantity','unit','unit_price','line_amount','tax_rate')}
                if any(v is not None for v in line.values()):lines.append(line)
            rate=value('printed_tax_rate')
            if rate:
                if rate not in ('0','0.20'):raise ReviewError('Use only an explicit printed 0 or 0.20 tax rate.')
                for line in lines:line['tax_rate']=rate
            candidate['line_items']=lines
            store.revise(tenant,case_id,int(data.get('revision','0')),candidate,data.get('reason',''),data.get('source_checked')=='yes')
            return RedirectResponse('/review/'+case_id,status_code=303)
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.post('/review/{case_id}/approve')
    async def approve(case_id:str,request:Request):
        data,invalid=await protected_form(request)
        if invalid is not None:return invalid
        try:
            store,tenant=context();store.approve(tenant,case_id,int(data.get('revision','0')),data.get('signature',''))
            return RedirectResponse('/review/'+case_id,status_code=303)
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.post('/review/{case_id}/submit')
    async def process(case_id:str,request:Request):
        data,invalid=await protected_form(request)
        if invalid is not None:return invalid
        try:
            store,tenant=context()
            await submit_case(connection,case_id,int(data.get('revision','0')))
            return RedirectResponse('/review/'+case_id,status_code=303)
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)

    @app.post('/review/{case_id}/mapping')
    async def configure_mapping(case_id:str,request:Request):
        data,invalid=await protected_form(request)
        if invalid is not None:return invalid
        try:
            store,tenant=context();case=store.get(tenant,case_id)
            if not case or case['revision']!=int(data.get('revision','0')):raise ReviewError('Stale mapping form. Reload the case.')
            mapping={'account_code':data.get('account_code',''),'tax_type':data.get('tax_type',''),'tax_rate':data.get('tax_rate','')}
            if mapping['tax_rate'] not in ('0','0.20'):raise ReviewError('Unsupported tax rate.')
            checker=DemoBills(connection,mapping);checker.invoice=case['candidate']
            await checker.preview()  # Reads organization, active account and tax applicability.
            if store.get(tenant,case_id)['revision']!=case['revision']:raise ReviewError('Invoice changed while mapping was being checked. Reload it.')
            store.save_mapping(tenant,case['candidate']['supplier']['name'],mapping['account_code'],mapping['tax_type'],mapping['tax_rate'],data.get('reason',''))
            return RedirectResponse('/review/'+case_id,status_code=303)
        except (ConnectionError,ExtractionError,ValueError,OSError,sqlite3.Error) as exc:return error(exc)
