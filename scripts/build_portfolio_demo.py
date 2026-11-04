"""Build the credential-free illustrated replay from original synthetic fixtures."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def build():
    records=[]
    for name,filename,outcome,reason in [
        ('Valid invoice','invoice_001','verified','Existing synthetic draft verified at GBP 95.16.'),
        ('Duplicate submission','invoice_001','duplicate','Separate email receipt, same document and business invoice. No second obligation.'),
        ('Missing invoice number','invoice_013','held','Invoice number is missing. Preserve the null and hold for source-based correction.'),
        ('Incorrect printed total','invoice_015','held','Printed total 553.13 disagrees with 455.00 + 91.00 = 546.00. Supplier mapping also needs configuration.'),
        ('Unsupported attachment',None,'unsupported','A text attachment is recorded and skipped. No extraction or accounting call.')]:
        invoice=json.loads((ROOT/f'fixtures/extraction/tuning/{filename}.json').read_text()) if filename else {}
        records.append({'name':name,'outcome':outcome,'reason':reason,'source':f'sources/{filename}.pdf' if filename else None,
            'supplier':(invoice.get('supplier') or {}).get('name'),'number':invoice.get('invoice_number'),
            'date':invoice.get('invoice_date'),'total':invoice.get('displayed_total'),
            'subtotal':invoice.get('subtotal'),'tax':invoice.get('tax'),'items':invoice.get('line_items',[])})
    (ROOT/'site/data.json').write_text(json.dumps(records,indent=2)+'\n')
    if (ROOT/'artifacts/phase-04-summary.json').exists():
        first=json.loads((ROOT/'artifacts/phase-04-summary.json').read_text())
        second=json.loads((ROOT/'artifacts/phase-04-additional-results.json').read_text())['summary']
        metrics={'evaluated_distinct_invoices':20,'matched_leaf_fields':first['matched_leaf_fields']+second['matched_fields'],
            'expected_leaf_fields':first['expected_leaf_fields']+second['expected_fields'],'invented_missing_values':0,
            'initial_valid_documents_held':1,'fixture_dataset_count':50,'prompt_sha256':first['prompt_sha256'],
            'limitations':'Small synthetic familiar-layout sample. Not production accuracy or scan/OCR coverage.',
            'live_acceptance':{'deliveries':5,'verified_arrivals':2,'held':2,'unsupported':1,'distinct_verified_drafts':1,
                'counted_total_gbp':'95.16','repeat_poll_new_deliveries':0,'new_model_calls':0,'new_creation_intents':0},
            'historical_estimated_extraction_usd':round(first['estimated_usd_at_peak_prices']+second['estimated_usd_at_peak_prices'],8),
            'cost_basis':'Historical peak-rate estimate checked 2026-11-02, including tuning and smoke calls. Not a billing statement.'}
        (ROOT/'artifacts/public/metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
    return records

if __name__=='__main__':
    print('Built',len(build()),'illustrated scenarios. No network or account access.')
