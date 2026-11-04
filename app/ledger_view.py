"""Read-only ledger UI plus explicit controlled sample submission forms."""
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.samples import SAMPLES

E=lambda value:html.escape(str(value if value is not None else '-'))

def render(snapshot,csrf):
    def table(headers,rows):
        if not rows:return '<p class="muted">No records yet.</p>'
        return '<div class="tablewrap"><table><thead><tr>'+''.join('<th>'+E(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+E(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    def stamp(value):return datetime.fromtimestamp(value,ZoneInfo('Asia/Kolkata')).strftime('%d %b %H:%M:%S IST')
    invoices=snapshot['invoices'];receipts=snapshot['receipts']
    forms=''
    for key,(label,_,_) in SAMPLES.items():
        forms+='<form method="post" action="/ledger/submit"><input type="hidden" name="csrf" value="'+E(csrf)+'"><input type="hidden" name="sample" value="'+E(key)+'"><button>'+E(label)+'</button></form>'
    known={i['id']:i['invoice_number'] for i in invoices}
    content='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Phase 3 | Invoice ledger</title><link rel="stylesheet" href="/phase-overview.css"></head><body><main><header><p class="eyebrow">INVOICE INTAKE / PHASE 03</p><h1>Every receipt.<br>One obligation.</h1><p class="lead">Durable invoice history, duplicate recognition, and recovery from uncertain Xero writes.</p><span class="badge">Live local ledger · Synthetic fixtures · UK Demo Company</span></header>'''
    content+='<section class="metrics"><article><span>RECEIPTS</span><strong>'+str(len(receipts))+'</strong></article><article><span>BUSINESS INVOICES</span><strong>'+str(len(invoices))+'</strong></article><article><span>VERIFIED</span><strong>'+str(sum(i['state']=='verified' for i in invoices))+'</strong></article><article><span>HELD RECEIPTS</span><strong>'+str(sum(r['outcome'].startswith('held') for r in receipts))+'</strong></article></section>'
    content+='<section class="panel"><h2>Run a controlled sample</h2><p>The first three samples refer to AP-2026-1001. Another valid invoice can create a separate draft. Every click is a distinct manual receipt. Conflict/invalid/unknown examples inject confirmed-data changes; their PDF stays unchanged. They test processing rules, not PDF interpretation.</p><div class="sampleforms">'+forms+'</div></section>'
    content+='<section class="panel"><h2>Business invoices</h2>'+table(['Reference','State','Xero bill ID','Last outcome'],[(i['invoice_number'],i['state'],i['xero_id'],i['message']) for i in invoices])+'</section>'
    content+='<section class="panel"><h2>Receipt history</h2>'+table(['Received','Filename','Outcome','Invoice','SHA-256','Submitted total'],[(stamp(r['created']),r['filename'],r['outcome'],known.get(r['invoice_id'],'No linked invoice'),r['sha256'][:16]+'…',json.loads(r['submitted_json']).get('displayed_total','-') if r.get('submitted_json') else 'Legacy: not captured') for r in receipts])+'</section>'
    content+='<section class="panel"><h2>Creation attempts</h2><p>Reconciled bills from Phase 2 have no Phase 3 creation attempt. Attempts here are durable intents recorded before a new write.</p>'+table(['Invoice','Operation ID','State','Xero ID'],[(known.get(a['invoice_id'],a['invoice_id']),a['id'],a['state'],a['xero_id']) for a in snapshot['attempts']])+'</section>'
    content+='<section class="panel"><h2>Recent events</h2>'+table(['Time','Event','Outcome'],[(stamp(e['created']),e['kind'],e['message']) for e in snapshot['events']])+'</section>'
    content+='''<section class="panel"><h2>What you should understand</h2><dl><dt>Receipt identity</dt><dd>The delivery. Re-polling one source key reuses its receipt; a new delivery creates another receipt.</dd><dt>File hash</dt><dd>SHA-256 of the bytes catches identical PDFs despite filenames.</dd><dt>Business identity</dt><dd>Tenant + confirmed supplier + invoice number links re-exported PDFs. Conflicting parsed values are held without replacing the original.</dd><dt>Creation intent</dt><dd>The payload and stable idempotency key are committed before a write. A crash in this window may require manual investigation.</dd><dt>Recovery</dt><dd>Retrieve by saved Xero ID or search supplier/reference. If the outcome remains unclear, do not create another bill.</dd></dl></section><section class="panel"><h2>Validate it yourself</h2><ol><li>Submit the original, renamed copy and re-export. Check three receipts link to one business invoice and Xero bill.</li><li>Submit the conflict and arithmetic error. Check holds; the original total stays unchanged.</li><li>Submit the second valid invoice. It should have its own business identity and one draft.</li><li>Restart the backend and reload this ledger. The records remain.</li></ol><p>Lost-response, concurrency and worker-exit cases are tested with simulated Xero responses. This app uses one worker for OAuth refresh; it is not a production multi-user deployment. These samples do not call DeepSeek or Gmail.</p></section><p><a href="/ledger">Refresh ledger</a> · <a href="/overview">Phase 2 overview</a> · <a href="/">Home</a></p><footer>Source PDFs and confirmed invoice data are stored locally. Tokens remain in separate encrypted storage.</footer></main></body></html>'''
    return content
