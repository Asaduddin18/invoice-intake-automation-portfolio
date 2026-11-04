import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.extraction import ExtractionError, extract_text_invoice, parse_invoice, pdf_text


def sample():
    original = json.loads((Path(__file__).parents[1] / 'fixtures/invoice_001.json').read_text())
    return {key: original[key] for key in (
        'supplier', 'invoice_number', 'invoice_date', 'due_date', 'currency',
        'line_items', 'subtotal', 'tax', 'displayed_total')}


def printed_sample():
    data = sample()
    data['supplier'] = {key: data['supplier'][key] for key in ('name', 'email', 'address')}
    return data


def test_pdf_text_retains_source_identity_and_pages():
    data = (Path(__file__).parents[1] / 'fixtures/invoice_001.pdf').read_bytes()
    result = pdf_text(data)
    assert '[PAGE 1]' in result
    assert 'AP-2026-1001' in result
    assert '95.16' in result


def test_invalid_pdf_and_non_schema_output_are_held():
    with pytest.raises(ExtractionError):
        pdf_text(b'not a PDF')
    with pytest.raises(ExtractionError):
        parse_invoice(' ')
    with pytest.raises(ExtractionError):
        parse_invoice(json.dumps(sample()))  # Reject fixture metadata in supplier.


def test_missing_fields_and_printed_errors_survive_schema_validation():
    data = printed_sample()
    data['invoice_number'] = None
    data['displayed_total'] = '999.99'
    result = parse_invoice(json.dumps(data))
    assert result['invoice_number'] is None
    assert result['displayed_total'] == '999.99'


def test_request_contains_only_supplied_text_and_prompt():
    def respond(request):
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        assert body['messages'][1]['content'].endswith('SOURCE TEXT ONLY')
        assert 'expected_validation' not in request.content.decode()
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(printed_sample())}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await extract_text_invoice('SOURCE TEXT ONLY', 'test-key', client=client)
    assert asyncio.run(run())['invoice']['displayed_total'] == '95.16'


def test_provider_error_is_sanitized_and_not_retried():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(402, text='sensitive provider detail')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await extract_text_invoice('SOURCE TEXT ONLY', 'test-key', client=client)
    with pytest.raises(ExtractionError, match='HTTP 402'):
        asyncio.run(run())
    assert len(calls) == 1

from app.extraction import extract_document, financial_issues, render_pages, critical
from app.extraction_runs import ExtractionRuns
from pypdf import PdfWriter
import io


def pdf_bytes():
    return (Path(__file__).parents[1] / 'fixtures/invoice_001.pdf').read_bytes()


def responder(outputs, requests):
    def handle(request):
        requests.append(json.loads(request.content))
        value = outputs.pop(0)
        if isinstance(value, int): return httpx.Response(value)
        return httpx.Response(200,json={'model':'deepseek-flash','usage':{'prompt_tokens':100,'completion_tokens':50},
            'choices':[{'finish_reason':'stop','message':{'content': value if isinstance(value,str) else json.dumps(value)}}]})
    return handle


def run_document(outputs, data=None, **kwargs):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder(outputs,calls))) as client:
            return await extract_document(data or pdf_bytes(),'test-key',client=client,**kwargs)
    return asyncio.run(run()), calls


def test_valid_text_does_not_pay_for_vision():
    result,calls=run_document([printed_sample()])
    assert result['status']=='extracted_valid' and len(calls)==1
    assert calls[0]['thinking']=={'type':'disabled'} and calls[0]['max_tokens']==4096


def test_missing_field_triggers_once_and_stays_missing():
    missing=printed_sample();missing['invoice_number']=None
    result,calls=run_document([missing,missing])
    assert len(calls)==2 and result['status']=='held'
    assert 'missing:invoice_number' in result['issues']
    assert isinstance(calls[1]['messages'][1]['content'],list)
    assert result['invoice']['invoice_number'] is None


def test_vision_disagreement_cannot_silently_replace_text():
    text=printed_sample();text['invoice_number']=None
    result,calls=run_document([text,printed_sample()])
    assert result['status']=='held' and 'text_vision_disagreement' in result['issues']
    assert result['attempts'][0]['invoice']['invoice_number'] is None


def test_printed_wrong_total_is_not_repaired():
    invalid=printed_sample();invalid['displayed_total']='999.99'
    result,calls=run_document([invalid,invalid])
    assert result['status']=='held' and 'arithmetic:total' in result['issues']
    assert result['invoice']['displayed_total']=='999.99' and len(calls)==2


def test_unusable_json_gets_one_vision_attempt_and_never_loops():
    result,calls=run_document([' ', 'invalid json'])
    assert result['status']=='held' and len(calls)==2


def test_no_text_routes_directly_to_vision():
    writer=PdfWriter();writer.add_blank_page(width=595,height=842)
    buffer=io.BytesIO();writer.write(buffer)
    result,calls=run_document([printed_sample()],buffer.getvalue())
    assert len(calls)==1 and result['status']=='extracted_valid'
    assert result['attempts'][-1]['method']=='vision'


def test_encrypted_and_over_page_limit_documents_never_call_provider():
    for encrypted in (True,False):
        writer=PdfWriter()
        for _ in range(1 if encrypted else 6):writer.add_blank_page(width=595,height=842)
        if encrypted:writer.encrypt('password')
        stream=io.BytesIO();writer.write(stream)
        result,calls=run_document([],stream.getvalue())
        assert result['status']=='held' and not calls


def test_api_errors_never_trigger_vision():
    for status in (401,402,429,503):
        result,calls=run_document([status])
        assert len(calls)==1 and result['status']=='held'
        assert result['fallback_reason'] is None


def test_numeric_normalization_and_zero_tax():
    one=printed_sample();two=printed_sample()
    two['line_items'][0]['quantity']='2.00'
    assert critical(one)==critical(two)
    one['tax']='0.00';one['displayed_total']=one['subtotal']
    for line in one['line_items']:line['tax_rate']='0'
    assert not financial_issues(one)
    one['line_items'][0]['quantity']='NaN'
    assert financial_issues(one)


def test_all_pages_render_and_persistence_detects_source_tampering(tmp_path):
    multi=(Path(__file__).parents[1]/'fixtures/extraction/tuning/invoice_009.pdf').read_bytes()
    assert len(render_pages(multi))==len(__import__('pypdf').PdfReader(io.BytesIO(multi)).pages)
    assert render_pages(pdf_bytes())[0].startswith(b'\x89PNG')
    async def run():
        store=ExtractionRuns(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder([printed_sample()],[]))) as client:
            record=await store.run(pdf_bytes(),'../../source.pdf','test',client=client)
        assert record['filename']=='source.pdf'
        assert ExtractionRuns(tmp_path).load(record['id'])['status']=='extracted_valid'
        assert store.source(record['id'])==pdf_bytes()
        (store.folder(record['id'])/'source.pdf').write_bytes(b'tampered')
        with pytest.raises(ExtractionError):store.source(record['id'])
        with pytest.raises(ValueError):store.load('../../env')
    asyncio.run(run())


def test_browser_held_submission_and_paid_run_csrf(tmp_path):
    from app.main import create_app,Settings
    from fastapi.testclient import TestClient
    import re
    app=create_app(Settings('id','secret',storage_dir=str(tmp_path)))
    client=TestClient(app)
    assert client.post('/extract/run',data={'sample':'invoice_001'}).status_code==403
    page=client.get('/extract')
    csrf=re.search('name="csrf" value="([^"]+)"',page.text).group(1)
    store=ExtractionRuns(tmp_path)
    record={'id':'a'*32,'status':'held','filename':'held.pdf','issues':['missing:invoice_number']}
    store.save(record)
    assert client.post('/extract/submit',data={'csrf':csrf,'run':record['id']},headers={'origin':'http://localhost:8080'}).status_code==409
    assert client.get('/extract',headers={'sec-fetch-site':'cross-site'}).status_code==403


def test_only_flash_model_is_allowed():
    result,calls=run_document([],model='deepseek-v4-pro')
    assert not calls and result['status']=='held'
    assert 'budgeted deepseek-flash' in result['issues'][0]


def test_cached_browser_run_never_calls_model_and_submission_is_idempotent(tmp_path,monkeypatch):
    import re
    import app.main as module
    from fastapi.testclient import TestClient
    from test_bills import FakeConnection
    fake=FakeConnection(tmp_path)
    monkeypatch.setattr(module,'XeroConnection',lambda *args:fake)
    store=ExtractionRuns(tmp_path)
    async def prepare():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder([printed_sample()],[]))) as client:
            return await store.run(pdf_bytes(),'invoice_001.pdf','test',client=client)
    saved=asyncio.run(prepare())
    async def forbidden(*args,**kwargs):
        raise AssertionError('Unexpected paid call for a saved sample')
    monkeypatch.setattr('app.extraction_runs.extract_document',forbidden)
    client=TestClient(module.create_app(module.Settings('id','secret',storage_dir=str(tmp_path))))
    page=client.get('/extract')
    csrf=re.search('name="csrf" value="([^"]+)"',page.text).group(1)
    response=client.post('/extract/run',data={'csrf':csrf,'sample':'invoice_001'},headers={'origin':'http://localhost:8080'},follow_redirects=False)
    assert response.status_code==303 and len(store.recent())==1
    for _ in range(2):
        result=client.post('/extract/submit',data={'csrf':csrf,'run':saved['id']},headers={'origin':'http://localhost:8080'},follow_redirects=False)
        assert result.status_code==303
    downstream=store.load(saved['id'])['downstream_result']
    assert downstream['status']=='verified' and downstream['reused_existing']
    assert len([x for x in fake.calls if x[0]=='POST' and x[1]=='/Invoices'])==1
