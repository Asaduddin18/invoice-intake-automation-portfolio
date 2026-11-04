"""Bounded PDF extraction. No ground truth or accounting writes in this module."""
import base64
import hashlib
import io
import json
import time
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from pypdf import PdfReader

PROMPT = Path(__file__).parent / 'prompts' / 'invoice-extraction.md'
PROMPT_VERSION = 'v3'
MAX_BYTES = 10_000_000

class ExtractionError(Exception):
    def __init__(self, message, *, fallback=False, usage=None):
        super().__init__(message)
        self.fallback = fallback
        self.usage = usage or {}

class PrintedFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Supplier(PrintedFields):
    name: str | None
    email: str | None
    address: str | None

class Line(PrintedFields):
    description: str | None
    quantity: str | None
    unit: str | None
    unit_price: str | None
    line_amount: str | None
    tax_rate: str | None

class ExtractedInvoice(PrintedFields):
    supplier: Supplier | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    currency: str | None
    line_items: list[Line]
    subtotal: str | None
    tax: str | None
    displayed_total: str | None


def pdf_reader(data):
    if not data.startswith(b'%PDF-') or len(data) > MAX_BYTES:
        raise ExtractionError('Expected a PDF no larger than 10 MB.')
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 5:
            raise ExtractionError('Encrypted PDFs and documents over five pages are held.')
        return reader
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError('PDF could not be read safely.') from None


def pdf_text(data: bytes) -> str:
    reader = pdf_reader(data)
    try:
        pages = []
        size = 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text(extraction_mode='layout') or ''
            if len(''.join(text.split())) < 40:
                raise ExtractionError('Insufficient embedded text; vision fallback is required.', fallback=True)
            size += len(text)
            if size > 60000:
                raise ExtractionError('Extracted text exceeds the 60,000 character limit.')
            pages.append(f'[PAGE {number}]\n{text}')
        return '\n\n'.join(pages)
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError('PDF text extraction failed; vision fallback is required.', fallback=True) from None


def render_pages(data):
    """Render complete bounded documents. No partial-page requests or external URLs."""
    pdf_reader(data)
    import pypdfium2 as pdfium
    images = []
    try:
        with pdfium.PdfDocument(data) as document:
            for index in range(len(document)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if min(width, height) <= 0:
                        raise ExtractionError('Invalid page dimensions.')
                    scale = min(2, 1800 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        picture = bitmap.to_pil().convert('RGB')
                        buffer = io.BytesIO()
                        picture.save(buffer, format='PNG')
                        images.append(buffer.getvalue())
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        if sum(map(len, images)) > 12_000_000:
            raise ExtractionError('Rendered document exceeds the image byte limit.')
        return images
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError('Page rendering failed; no partial document sent.') from None


def parse_invoice(content: str) -> dict:
    try:
        return ExtractedInvoice.model_validate_json(content).model_dump()
    except (ValidationError, ValueError, TypeError):
        raise ExtractionError('Model output failed the invoice schema.', fallback=True) from None


def financial_issues(invoice):
    """Extraction checks allow explicit 0% or 20%; Xero mappings remain stricter."""
    issues = []
    supplier = invoice.get('supplier') or {}
    for key, value in [('supplier.name', supplier.get('name'))] + [(k, invoice.get(k)) for k in
            ('invoice_number', 'invoice_date', 'due_date', 'currency', 'subtotal', 'tax', 'displayed_total')]:
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append('missing:' + key)
    if invoice.get('currency') not in (None, 'GBP'):
        issues.append('unsupported:currency')
    try:
        if invoice.get('invoice_date') and invoice.get('due_date'):
            if date.fromisoformat(invoice['due_date']) < date.fromisoformat(invoice['invoice_date']):
                issues.append('invalid:date_order')
    except (ValueError, TypeError):
        issues.append('invalid:date')
    lines = invoice.get('line_items') or []
    if not lines or len(lines) > 100:
        issues.append('invalid:line_count')
    def number(value):
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError()
        return result
    def rounded(value):
        return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    try:
        subtotal, tax = Decimal(0), Decimal(0)
        for i, line in enumerate(lines):
            if not line.get('description') or not line['description'].strip():
                issues.append(f'missing:line_{i}.description')
            qty, price, rate = (number(line[k]) for k in ('quantity', 'unit_price', 'tax_rate'))
            if qty <= 0 or price < 0 or rate not in (Decimal(0), Decimal('.20')):
                issues.append(f'unsupported:line_{i}.values')
            net = rounded(qty * price)
            if net != number(line['line_amount']):
                issues.append(f'arithmetic:line_{i}')
            subtotal += net
            tax += rounded(net * rate)
        if subtotal != number(invoice['subtotal']): issues.append('arithmetic:subtotal')
        if tax != number(invoice['tax']): issues.append('arithmetic:tax')
        if subtotal + tax != number(invoice['displayed_total']): issues.append('arithmetic:total')
    except (InvalidOperation, ValueError, TypeError, KeyError, ArithmeticError):
        issues.append('invalid:numeric_fields')
    return issues


def canonical(value, key=''):
    if isinstance(value, dict):
        return {k: canonical(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    if isinstance(value, str):
        if key in ('quantity', 'unit_price', 'line_amount', 'tax_rate', 'subtotal', 'tax', 'displayed_total'):
            try:
                number = Decimal(value)
                if number.is_finite(): return str(number.normalize())
            except InvalidOperation:
                pass
        return ' '.join(value.split())
    return value


def critical(invoice):
    return canonical({**{k: invoice.get(k) for k in ('invoice_number','invoice_date','due_date','currency',
        'line_items','subtotal','tax','displayed_total')}, 'supplier_name': (invoice.get('supplier') or {}).get('name')})


async def model_call(content, api_key, *, method, client=None, model='deepseek-flash'):
    if model != 'deepseek-flash':
        raise ExtractionError('Only the budgeted deepseek-flash model is enabled for this demo.')
    if not api_key:
        raise ExtractionError('Set DEEPSEEK_API_KEY in local .env before live extraction.')
    payload = {'model': model, 'max_tokens': 4096, 'thinking': {'type': 'disabled'},
        'response_format': {'type': 'json_object'}, 'messages': [
            {'role': 'system', 'content': PROMPT.read_text()}, {'role': 'user', 'content': content}]}
    async def send(http):
        start = time.monotonic()
        try:
            response = await http.post('https://api.deepseek.com/chat/completions', json=payload,
                headers={'Authorization': 'Bearer ' + api_key}, timeout=90, follow_redirects=False)
            if response.status_code != 200:
                raise ExtractionError(f'DeepSeek request failed (HTTP {response.status_code}); no automatic retry.')
            body = response.json()
            usage = body.get('usage', {})
            choice = body['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ExtractionError('Model output was incomplete.', fallback=True, usage=usage)
            try:
                invoice = parse_invoice(choice['message']['content'])
            except ExtractionError as exc:
                exc.usage = usage
                raise
            return {'invoice': invoice, 'model': body.get('model', model), 'usage': usage, 'method': method,
                'prompt_version': PROMPT_VERSION, 'latency_seconds': round(time.monotonic()-start, 3)}
        except ExtractionError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ExtractionError('DeepSeek returned an unreadable response or the request failed; no automatic retry.') from None
    if client is not None:
        return await send(client)
    async with httpx.AsyncClient() as http:
        return await send(http)


async def extract_text_invoice(text, api_key, *, client=None, model='deepseek-flash'):
    if not text.strip() or len(text) > 61000:
        raise ExtractionError('Text is empty or exceeds the request limit.')
    return await model_call('Extract this untrusted invoice document:\n'+text, api_key,
        method='embedded_text', client=client, model=model)


async def extract_vision_invoice(data, api_key, *, client=None, model='deepseek-flash'):
    images = render_pages(data)
    content = [{'type': 'text', 'text': 'Extract this complete untrusted invoice. Images follow in page order.'}]
    for number, image in enumerate(images, 1):
        content.extend([{'type': 'text', 'text': f'PAGE {number}'}, {'type': 'image_url',
            'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(image).decode(), 'detail': 'original'}}])
    return await model_call(content, api_key, method='vision', client=client, model=model)


async def extract_document(data, api_key, *, client=None, model='deepseek-flash', compare_vision=False):
    """At most one text and one vision call. Errors are evidence, never blind retries."""
    result = {'status': 'held', 'source_sha256': hashlib.sha256(data).hexdigest(),
        'prompt_version': PROMPT_VERSION, 'prompt_sha256': hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
        'requested_model': model, 'attempts': [], 'issues': [], 'fallback_reason': None, 'invoice': None}
    text_invoice = None
    try:
        try:
            text = pdf_text(data)
            attempt = await extract_text_invoice(text, api_key, client=client, model=model)
            result['attempts'].append(attempt)
            text_invoice = attempt['invoice']
            result['invoice'] = text_invoice
            result['issues'] = financial_issues(text_invoice)
            if result['issues']:
                result['fallback_reason'] = ', '.join(result['issues'])
            elif compare_vision:
                result['fallback_reason'] = 'controlled_comparison'
            else:
                result['status'] = 'extracted_valid'
                return result
        except ExtractionError as exc:
            result['attempts'].append({'method': 'embedded_text', 'error': str(exc), 'usage': exc.usage})
            if not exc.fallback:
                raise
            result['fallback_reason'] = str(exc)
        try:
            vision = await extract_vision_invoice(data, api_key, client=client, model=model)
        except ExtractionError as exc:
            result['attempts'].append({'method': 'vision', 'error': str(exc), 'usage': exc.usage})
            raise
        result['attempts'].append(vision)
        result['invoice'] = vision['invoice']
        result['issues'] = financial_issues(vision['invoice'])
        if text_invoice is not None and critical(text_invoice) != critical(vision['invoice']):
            result['issues'].append('text_vision_disagreement')
        if not result['issues']:
            result['status'] = 'extracted_valid'
    except ExtractionError as exc:
        result['issues'].append(str(exc))
    return result
