"""Explicit demo accounting mappings, validation and draft-only payloads."""
import json
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from app.connection import ConnectionError

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'invoice_001.json'
ACCOUNT_CODE = '325'
TAX_TYPE = 'INPUT2'
CONTACT_NUMBER = 'PORTFOLIO-ALDER-001'
D = Decimal

def amount(value):
    return D(str(value)).quantize(D('0.01'), rounding=ROUND_HALF_UP)

def validate(invoice, tax_rate='0.20'):
    if not isinstance(invoice,dict) or invoice.get('currency') != 'GBP' or not isinstance(invoice.get('invoice_number'),str) or not invoice['invoice_number'].strip() or not isinstance(invoice.get('supplier'),dict) or not invoice['supplier'].get('name'):
        raise ConnectionError('Missing supplier/invoice identity or unsupported currency.')
    try:
        issued, due = date.fromisoformat(invoice['invoice_date']), date.fromisoformat(invoice['due_date'])
        if due < issued or not invoice['line_items']:
            raise ValueError()
        total_net, total_tax = D('0'), D('0')
        for line in invoice['line_items']:
            qty, price = D(line['quantity']), D(line['unit_price'])
            if not qty.is_finite() or not price.is_finite() or qty <= 0 or price < 0 or D(line['tax_rate']) != D(tax_rate):
                raise ValueError()
            net = amount(qty * price)
            if net != D(line['line_amount']):
                raise ValueError()
            total_net += net
            total_tax += amount(net * D(tax_rate))
        if total_net != D(invoice['subtotal']) or total_tax != D(invoice['tax']) or total_net + total_tax != D(invoice['displayed_total']):
            raise ValueError()
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise ConnectionError('Invoice dates, quantities, prices or totals failed validation.') from None


def build_payload(invoice, contact_id, mapping=None):
    mapping = mapping or {'account_code': ACCOUNT_CODE, 'tax_type': TAX_TYPE, 'tax_rate': '0.20'}
    validate(invoice, tax_rate=mapping['tax_rate'])
    return {'Type': 'ACCPAY', 'Contact': {'ContactID': contact_id}, 'InvoiceNumber': invoice['invoice_number'], 'Date': invoice['invoice_date'], 'DueDate': invoice['due_date'], 'CurrencyCode': 'GBP', 'Status': 'DRAFT', 'LineAmountTypes': 'Exclusive', 'LineItems': [{'Description': line['description'], 'Quantity': float(D(line['quantity'])), 'UnitAmount': float(D(line['unit_price'])), 'AccountCode': mapping['account_code'], 'TaxType': mapping['tax_type']} for line in invoice['line_items']]}


def bill_matches(bill, expected):
    errors = []
    for key in ('Type','InvoiceNumber','CurrencyCode','Status','LineAmountTypes'):
        if bill.get(key) != expected[key]: errors.append(key)
    if bill.get('Contact',{}).get('ContactID') != expected['Contact']['ContactID']: errors.append('ContactID')
    def normalize_date(value):
        if isinstance(value,str) and value.startswith('/Date('):
            from datetime import datetime, timezone
            ms=int(re.search(r'-?\d+',value).group());return datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()
        return str(value)[:10]
    for key in ('Date','DueDate'):
        if normalize_date(bill.get(key)) != expected[key]: errors.append(key)
    lines=bill.get('LineItems',[])
    if len(lines)!=len(expected['LineItems']): errors.append('LineItems count')
    for actual, wanted in zip(lines,expected['LineItems']):
        for key in ('Description','AccountCode','TaxType'):
            if actual.get(key)!=wanted[key]: errors.append('LineItems.'+key)
        for key in ('Quantity','UnitAmount'):
            if D(str(actual.get(key,'NaN')))!=D(str(wanted[key])): errors.append('LineItems.'+key)
    return errors

class DemoBills:
    def __init__(self, connection, mapping=None):
        self.connection=connection
        self.mapping=mapping or {'account_code': ACCOUNT_CODE, 'tax_type': TAX_TYPE, 'tax_rate': '0.20'}
        self.invoice=json.loads(FIXTURE.read_text())

    async def preview(self):
        validate(self.invoice, tax_rate=self.mapping['tax_rate'])
        account_code, tax_type = self.mapping['account_code'], self.mapping['tax_type']
        orgs=(await self.connection.request('GET','/Organisation')).get('Organisations',[])
        if len(orgs)!=1 or orgs[0].get('BaseCurrency')!='GBP' or orgs[0].get('CountryCode')!='GB':
            raise ConnectionError('Expected a UK organisation with GBP base currency.')
        accounts=(await self.connection.request('GET','/Accounts')).get('Accounts',[])
        taxes=(await self.connection.request('GET','/TaxRates')).get('TaxRates',[])
        account=next((a for a in accounts if a.get('Code')==account_code and a.get('Status')=='ACTIVE' and a.get('Type') in ('DIRECTCOSTS','EXPENSE','OVERHEADS')),None)
        tax=next((t for t in taxes if t.get('TaxType')==tax_type and t.get('Status')=='ACTIVE' and t.get('CanApplyToExpenses') and D(str(t.get('EffectiveRate')))==D(self.mapping['tax_rate'])*100),None)
        if not account or not tax:
            raise ConnectionError('The agreed demo account/tax mapping is unavailable. Inspect current settings.')
        return {'invoice':self.invoice,'mapping':{'account_code':account_code,'account_name':account['Name'],'tax_type':tax_type,'tax_name':tax['Name']},'target_status':'DRAFT','total':self.invoice['displayed_total']}

    async def create(self):
        # All exposed writes now pass through the durable Phase 3 ledger.
        from app.pipeline import InvoicePipeline
        return await InvoicePipeline(self.connection).submit(self.invoice, FIXTURE.with_suffix('.pdf').read_bytes(), 'invoice_001.pdf')
