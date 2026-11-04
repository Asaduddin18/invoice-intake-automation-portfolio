"""Prepare a labeled workflow demo from saved extractions. Zero model/API calls."""
import copy
import json
import os
import uuid
from pathlib import Path

from app.connection import TokenStore
from app.extraction_runs import ExtractionRuns
from app.review import ReviewStore

ROOT=Path(__file__).resolve().parents[1]


def main(practice=False):
    runtime=ROOT/'.runtime'
    tenant=TokenStore(runtime).load()['tenant_id']
    runs=ExtractionRuns(runtime)
    review=ReviewStore(runtime)
    manifest=runtime/'review/demo-cases.json'
    if manifest.exists() and not practice:
        print('Existing demo:',manifest.read_text());return
    paths=list(runs.directory.glob('*/result.json'))
    records=[json.loads(path.read_text()) for path in paths]
    original=next(r for r in records if r['filename']=='invoice_001.pdf' and r.get('status')=='extracted_valid' and r.get('prompt_version')=='v3')
    clone=copy.deepcopy(original)
    clone['id']=uuid.uuid4().hex
    clone['filename']='phase05-practice.pdf' if practice else 'phase05-correction-demo.pdf'
    clone.pop('downstream_result',None)
    clone['attempts']=[]
    clone['fixture_source_run']=original['id']
    clone['invoice']['line_items'][0]['unit_price']=None
    clone['status']='held';clone['issues']=['invalid:numeric_fields']
    clone['fixture_note']='Explicit synthetic fault injection for workflow demonstration: saved extraction unit price removed. The source PDF is unchanged. This is not a claimed DeepSeek failure.'
    runs.save(clone)
    source=runs.folder(clone['id'])/'source.pdf'
    source.write_bytes(runs.source(original['id']));os.chmod(source,0o600)
    review.ensure_case(tenant,clone['id'])
    chosen={'correction_demo':clone['id'],'original_valid':original['id']}
    if practice:
        print(json.dumps({'practice_case':clone['id'],'url':'http://localhost:8080/review/'+clone['id']},indent=2));return
    for filename,key in [('invoice_015.pdf','printed_total_error'),('invoice_017.pdf','unmapped_supplier'),('invoice_039.pdf','reading_disagreement')]:
        record=next(r for r in reversed(records) if r['filename']==filename and r.get('prompt_version')=='v3')
        chosen[key]=record['id'];review.ensure_case(tenant,record['id'])
    manifest.write_text(json.dumps(chosen,indent=2)+'\n');os.chmod(manifest,0o600)
    print(json.dumps(chosen,indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--practice',action='store_true',help='Create a new labeled practice case without model calls.')
    main(parser.parse_args().practice)
