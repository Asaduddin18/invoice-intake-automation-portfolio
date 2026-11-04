"""Local extraction evidence and explicit downstream submission."""
import html
import json


def render(runs, csrf, choices):
    escape = html.escape
    forms = ''.join('<form method="post" action="/extract/run"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="sample" value="'+escape(name)+'"><button>Extract '+escape(name)+'</button></form>' for name in choices)
    sections = []
    seen = set()
    latest = []
    for run in runs:
        if run['filename'] not in seen:
            latest.append(run); seen.add(run['filename'])
    for run in latest:
        run_id = run['id']
        invoice = run.get('invoice') or {}
        downstream = run.get('downstream_result') or {}
        supplier = (invoice.get('supplier') or {}).get('name') or 'Missing supplier'
        fields = [("Supplier",supplier),("Invoice",invoice.get('invoice_number')),
            ("Issued",invoice.get('invoice_date')),("Due",invoice.get('due_date')),
            ("Subtotal",invoice.get('subtotal')),("Tax",invoice.get('tax')),("Total GBP",invoice.get('displayed_total'))]
        summary = '<dl class="invoicefields">'+''.join('<dt>'+escape(k)+'</dt><dd>'+escape(str(v) if v is not None else 'Missing')+'</dd>' for k,v in fields)+'</dl>'
        if run.get('issues'):
            summary += '<p><strong>Held because:</strong> '+escape(', '.join(run['issues']))+'</p>'
        if run.get('fallback_reason'):
            summary += '<p>Vision reason: '+escape(run['fallback_reason'])+'</p>'
        if downstream:
            summary += '<p><strong>Accounting result:</strong> '+escape(downstream.get('status','unknown'))
            if downstream.get('invoice_id') and downstream.get('status') == 'verified':
                link = 'https://go.xero.com/AccountsPayable/Edit.aspx?InvoiceID='+escape(downstream['invoice_id'])
                summary += ' · <a href="'+link+'">Open Xero draft '+escape(downstream.get('invoice_number',''))+'</a>'
            elif downstream.get('message'):
                summary += ' · '+escape(downstream['message'])
            summary += '</p>'
        submit = ''
        if run['status'] == 'extracted_valid':
            submit = '<form method="post" action="/extract/submit"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="run" value="'+run_id+'"><button>Send through duplicate and Xero checks</button></form>'
        detail = escape(json.dumps(run, indent=2))
        sections.append('<section class="panel"><h2>'+escape(run['filename'])+' · '+escape({'extracted_valid':'Ready for accounting checks','held':'Held','processing':'Processing'}.get(run['status'],run['status']))+'</h2>'+summary+'<p><a href="/extract/source/'+run_id+'">Open source PDF</a> · <a href="/review/'+run_id+'">Open workflow and corrections</a></p>'+submit+'<details><summary>Fields, checks and model attempts</summary><pre>'+detail+'</pre></details></section>')
    return '<!doctype html><html><head><meta charset="utf-8"><title>PDF extraction</title><link rel="stylesheet" href="/phase-overview.css"></head><body><main><a href="/">Home</a> · <a href="/ledger">Invoice ledger</a><h1>Phase 4: PDF extraction</h1><p>Synthetic invoices only. Text first; one vision fallback for unclear or invalid results. Conflicting results stay held. Source and attempt evidence stay on this machine.</p><p>Model: deepseek-flash, reasoning off. Extraction uses paid calls; repeating a saved sample reuses its result. Extracting does not create a Xero bill. The submit button uses existing supplier, tax and duplicate checks. Only the agreed Alder / 20% mapping can reach Xero. Review policy and corrections are available in the Phase 5 workflow.</p><h2>Try a sample</h2><div class="sampleforms">'+forms+'</div><h2>Latest run per sample</h2><p>Older runs remain in local evidence storage.</p>'+''.join(sections)+'</main></body></html>'
