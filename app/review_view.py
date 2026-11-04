"""Local workflow screens. Source-first editing and explicit configuration."""
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.review import DEMO_SUPPLIERS

E = html.escape

def issue_label(issue):
    labels={'text_vision_disagreement':'Text and vision disagree. Check the source before resolving.', 'arithmetic:subtotal':'Printed subtotal disagrees with line calculations.', 'arithmetic:tax':'Printed tax disagrees with line calculations.', 'arithmetic:total':'Printed total disagrees with the calculated total.', 'invalid:numeric_fields':'A quantity, price, amount or tax rate is missing or invalid.'}
    if issue.startswith('missing:'):return 'Missing '+issue.split(':',1)[1].replace('_',' ')+'.'
    return labels.get(issue,issue)

LABELS = {'ready':'Ready to process','held':'Held for correction','awaiting_approval':'Awaiting approval',
    'verified':'Verified Xero draft','uncertain':'Needs reconciliation','processing':'Processing'}


def hidden(name, value):
    return '<input type="hidden" name="'+E(name)+'" value="'+E(str(value),quote=True)+'">'


def field(name, label, value, kind='text'):
    return '<label>'+E(label)+'<input name="'+E(name)+'" type="'+kind+'" value="'+E(str(value) if value is not None else '',quote=True)+'"></label>'


def form(action, csrf, body):
    return '<form method="post" action="'+action+'">'+hidden('csrf',csrf)+body+'</form>'


def page(title, body):
    return '<!doctype html><html><head><meta charset="utf-8"><title>'+E(title)+'</title><link rel="stylesheet" href="/phase-overview.css"></head><body><main><nav><a href="/">Home</a> · <a href="/review">Workflow</a> · <a href="/extract">Extractions</a> · <a href="/ledger">Accounting ledger</a></nav>'+body+'</main></body></html>'


def queue(store, tenant, runs, csrf, notice=''):
    policy=store.policy(tenant)
    mode='Automatic' if policy['mode']=='automatic' else 'Approval required'
    controls='<h2>Processing policy: '+mode+'</h2><p>Automatic mode processes valid, configured invoices without approval when intake runs. Approval-required mode waits for the current revision to be approved. Invalid invoices stay held in both modes.</p>'
    controls+=form('/review/policy',csrf,'<label>Review mode<select name="mode"><option value="automatic"'+(' selected' if policy['mode']=='automatic' else '')+'>Automatic</option><option value="required"'+(' selected' if policy['mode']=='required' else '')+'>Approval required</option></select></label><label>Reason<input name="reason" required minlength="5" maxlength="1000"></label><button>Save policy</button>')
    rows=[]
    for case in store.all(tenant):
        status,issues=store.decision(tenant,case)
        outcome=case.get('outcome') or {}
        shown=outcome.get('status') if outcome.get('status') in ('verified','uncertain','processing') else status
        invoice=case['candidate'] or {}
        rows.append('<tr><td><a href="/review/'+case['id']+'">'+E(case['filename'])+'</a></td><td>'+E(str(invoice.get('invoice_number') or 'Missing'))+'</td><td>'+E(LABELS.get(shown,shown))+'</td><td>'+str(case['revision'])+'</td><td>'+E('; '.join(issue_label(i) for i in issues) or outcome.get('message',''))+'</td></tr>')
    choices=[];seen=set()
    for run in runs:
        if run['filename'] in seen or run.get('status')=='processing':continue
        seen.add(run['filename'])
        choices.append('<option value="'+run['id']+'">'+E(run['filename'])+'</option>')
    intake=form('/review/intake',csrf,'<label>Saved extraction<select name="run">'+''.join(choices)+'</select></label><button>Run intake using current policy</button>')
    policy_history='<section class="panel"><h2>Policy and mapping history</h2>'
    for event in store.history(tenant):
        if event['kind'] in ('policy_changed','mapping_changed'):
            policy_history+='<details><summary>'+E(event['kind'])+' · '+datetime.fromtimestamp(event['created'],ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y %H:%M IST')+'</summary><pre>'+E(event['detail'])+'</pre></details>'
    policy_history+='</section>'
    return page('Invoice workflow','<h1>Phase 5: invoice workflow</h1><p>Saved synthetic data only. This screen makes no DeepSeek calls. Policy, corrections and accounting outcomes survive restart.</p>'+(('<p class="notice">'+E(notice)+'</p>') if notice else '')+'<section class="panel">'+controls+'</section><section class="panel"><h2>Process a saved extraction</h2><p>Intake validates and applies the current policy. Valid automatic-mode invoices can create a Xero draft. Held cases remain visible.</p>'+intake+'</section><section class="panel"><h2>Invoices and exceptions</h2><div class="tablewrap"><table><thead><tr><th>Source</th><th>Invoice</th><th>Status</th><th>Revision</th><th>Reason</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></section>'+policy_history)


def detail(store, tenant, case, csrf, pages=1, page_number=1, notice=''):
    status,issues=store.decision(tenant,case)
    invoice=case['candidate'] or {}
    supplier=invoice.get('supplier') or {}
    outcome=case.get('outcome') or {}
    locked=case['financial_locked']
    base='/review/'+case['id']
    summary='<h1>'+E(str(invoice.get('invoice_number') or case['filename']))+'</h1><p class="badge">'+E(LABELS.get(outcome.get('status') if locked and outcome.get('status') in ('verified','uncertain','processing') else status,status))+' · revision '+str(case['revision'])+'</p>'
    if notice:summary+='<p class="notice">'+E(notice)+'</p>'
    if issues:summary+='<ul>'+''.join('<li>'+E(issue_label(issue))+'</li>' for issue in issues)+'</ul>'
    if outcome:
        summary+='<p><strong>Last processing result:</strong> '+E(LABELS.get(outcome.get('status'),outcome.get('status','Unknown')))+'</p><details><summary>Accounting identifiers and result</summary><pre>'+E(json.dumps(outcome,indent=2))+'</pre></details>'
        if outcome.get('status')=='verified':
            summary+='<p><a href="https://go.xero.com/AccountsPayable/Edit.aspx?InvoiceID='+E(outcome['invoice_id'])+'">Open verified Xero draft</a></p>'
    summary+=form(base+'/submit',csrf,hidden('revision',case['revision'])+'<button>'+('Reconcile saved result' if locked else 'Process current revision')+'</button>')
    if status=='awaiting_approval':
        summary+=form(base+'/approve',csrf,hidden('revision',case['revision'])+hidden('signature',store.signature(tenant,case))+'<button>Approve this revision</button>')
    editor=''
    if not locked:
        inputs=''.join(field('supplier_'+key,'Supplier '+key,supplier.get(key)) for key in ('name','email','address'))
        inputs+=''.join(field(key,key.replace('_',' ').capitalize(),invoice.get(key)) for key in ('invoice_number','invoice_date','due_date','currency','subtotal','tax','displayed_total'))
        lines=invoice.get('line_items') or []
        count=min(len(lines)+1,100)
        inputs+=hidden('line_count',count)
        for i in range(count):
            line=lines[i] if i<len(lines) else {}
            inputs+='<fieldset><legend>'+('Line '+str(i+1) if line else 'Optional new line')+'</legend>'
            inputs+=''.join(field('line_'+str(i)+'_'+key,key.replace('_',' ').capitalize(),line.get(key)) for key in ('description','quantity','unit','unit_price','line_amount','tax_rate'))
            if line:inputs+='<label><input type="checkbox" name="remove_'+str(i)+'" value="yes">Remove this line</label>'
            inputs+='</fieldset>'
        inputs+=field('printed_tax_rate','Optional: copy a single tax rate printed for the whole invoice to every line (0 or 0.20)',None)
        inputs+='<label>Correction or resolution reason<textarea name="reason" required minlength="5" maxlength="1000"></textarea></label><label><input type="checkbox" name="source_checked" value="yes" required>I checked these values against the source. I am correcting extraction or resolving a reading difference. Printed financial errors must remain held.</label><button>Save correction and revalidate</button>'
        editor=form(base+'/correct',csrf,hidden('revision',case['revision'])+inputs)
    else:
        editor='<p>Financial fields are locked while an accounting operation exists. Use reconciliation; do not change the source values to force another bill.</p><dl class="invoicefields">'+''.join('<dt>'+E(key.replace('_',' ').capitalize())+'</dt><dd>'+E(str(invoice.get(key) or 'Missing'))+'</dd>' for key in ('invoice_number','invoice_date','due_date','currency','subtotal','tax','displayed_total'))+'</dl><details><summary>Recorded invoice fields</summary><pre>'+E(json.dumps(invoice,indent=2))+'</pre></details>'
    image_links=' · '.join('<a href="'+base+'?page='+str(i)+'">Page '+str(i)+'</a>' for i in range(1,pages+1))
    source='<section class="panel sourcepanel"><h2>Source evidence</h2><p><a href="'+base+'/source">Open searchable PDF</a> · '+image_links+'</p><img class="sourcepage" alt="Synthetic invoice source page '+str(page_number)+'" src="'+base+'/page/'+str(page_number)+'"><p class="muted">SHA-256: '+E(case['source_sha256'])+'</p></section>'
    mapping=store.mapping(tenant,supplier.get('name'))
    mapping_form=''
    if supplier.get('name') in DEMO_SUPPLIERS:
        mapping_form=form(base+'/mapping',csrf,hidden('revision',case['revision'])+field('account_code','Xero expense account code',(mapping or {}).get('account_code','325'))+field('tax_type','Xero tax type',(mapping or {}).get('tax_type','INPUT2'))+'<label>Explicit invoice tax rate<select name="tax_rate"><option value="0.20">20%</option><option value="0">0%</option></select></label><label>Mapping reason<input name="reason" required minlength="5" maxlength="1000"></label><button>Validate live Xero settings and save mapping</button>')
    mapping_panel='<section class="panel"><h2>Supplier and accounting mapping</h2><p>Only the four fictional dataset suppliers can be configured. Saving reads Xero settings and makes no accounting write. Every mapping change invalidates approval through its signature. One uniform tax rate is configured per supplier.</p><pre>'+E(json.dumps(mapping,indent=2))+'</pre>'+mapping_form+'</section>'
    history='<section class="panel"><h2>History</h2>'
    for event in store.history(tenant,case['id']):
        history+='<details><summary>'+E(event['kind'])+' · '+datetime.fromtimestamp(event['created'],ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y %H:%M IST')+'</summary><pre>'+E(event['detail'])+'</pre></details>'
    history+='</section>'
    return page('Review '+str(invoice.get('invoice_number') or case['filename']),summary+'<div class="reviewgrid">'+source+'<section class="panel editor"><h2>Extracted fields and corrections</h2>'+editor+'</section></div>'+mapping_panel+history)
