"""Controlled manual submissions; no PDF extraction is claimed in Phase 3."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'fixtures'
SAMPLES={
 'original':('Original invoice AP-2026-1001','invoice_001.json','invoice_001.pdf'),
 'renamed':('Same PDF, different filename','invoice_001.json','invoice_001.pdf'),
 'reexport':('Re-exported PDF, same business invoice','invoice_001.json','invoice_001_reexport.pdf'),
 'second':('Another valid invoice AP-2026-1004','invoice_004.json','invoice_004.pdf'),
 'conflict':('Injected parsed-value conflict: same invoice identity','invoice_001.json','invoice_001.pdf'),
 'invalid':('Injected arithmetic error: must be held','invoice_001.json','invoice_001.pdf'),
 'unknown':('Injected unknown supplier: must be held','invoice_001.json','invoice_001.pdf'),
}

def sample(key):
 if key not in SAMPLES:raise ValueError('Unknown sample')
 label,j,p=SAMPLES[key];invoice=json.loads((ROOT/j).read_text());content=(ROOT/p).read_bytes();filename=p
 if key=='renamed':filename='forwarded_invoice_copy.pdf'
 if key=='conflict':
  invoice['line_items'][0]['unit_price']='13.35';invoice['line_items'][0]['line_amount']='26.70'
  invoice['subtotal']='81.30';invoice['tax']='16.26';invoice['displayed_total']='97.56'
 if key=='invalid':invoice['displayed_total']='100.00'
 if key=='unknown':invoice['supplier']['name']='Unknown Demo Supplier'
 return invoice,content,filename
