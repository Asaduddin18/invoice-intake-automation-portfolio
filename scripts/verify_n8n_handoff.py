"""Fresh-import and restart acceptance using existing synthetic demo connections.

Creates new local workflow IDs, reuses only the existing credential reference and
leaves original editor workflows untouched. No credential values are exported.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from verify_n8n_workflows import decode_execution

ROOT=Path(__file__).resolve().parents[1]


def verify(context):
    docker=['docker']+(['--context',context] if context else [])
    compose=['docker-compose']+(['--context',context] if context else [])
    private=ROOT/'.runtime/n8n';private.mkdir(exist_ok=True)
    def call(args,input=None,timeout=90,check=True):
        return subprocess.run(args,input=input,text=True,capture_output=True,cwd=ROOT,check=check,timeout=timeout)
    def counts():
        with sqlite3.connect('file:'+str(ROOT/'.runtime/ledger/invoices.sqlite3')+'?mode=ro',uri=True) as db:
            return {'ledger_invoices':db.execute('SELECT count(*) FROM invoices').fetchone()[0],
                'bill_creation_intents':db.execute('SELECT count(*) FROM attempts').fetchone()[0],
                'saved_extractions':len(list((ROOT/'.runtime/extractions').glob('*/result.json')))}
    ids=call([*docker,'ps','-q','--filter','label=com.docker.compose.project=northmere-invoice-learning','--filter','label=com.docker.compose.service=n8n']).stdout.split()
    if len(ids)!=1:raise RuntimeError('Start one n8n service first.')
    container=ids[0];prefix='p11'+uuid.uuid4().hex[:12]
    def export(id,file):
        call([*docker,'exec',container,'n8n','export:workflow','--id='+id,'--output=/home/node/.n8n/'+file])
        return json.loads(call([*docker,'exec',container,'node','-e',"console.log(require('fs').readFileSync('/home/node/.n8n/"+file+"','utf8'))"]).stdout)[0]
    reference=export('northmerePhase09','phase-11-reference.json')
    binding=next(n['credentials']['httpHeaderAuth'] for n in reference['nodes'] if n.get('credentials',{}).get('httpHeaderAuth'))
    helper=prefix+'Errors';workflows=[]
    for kind in ('errors','intake','reports'):
        w=json.loads((ROOT/'deploy/n8n/phase-10'/('phase-10-'+kind+'.json')).read_text())
        w['id']=prefix+kind.title();w['name']='Phase 11 acceptance - '+kind
        if kind=='errors':w['id']=helper
        else:w['settings']['errorWorkflow']=helper
        for n in w['nodes']:
            if n['type'].endswith('httpRequest'):n['credentials']={'httpHeaderAuth':binding}
            if n['type'].endswith('executeWorkflow'):n['parameters']['workflowId']['value']=helper
        workflows.append(w)
    status=json.loads((ROOT/'deploy/n8n/phase-09-status.json').read_text())
    status['id']=prefix+'Status';status['name']='Phase 11 acceptance - credential after restart'
    status['active']=False
    for n in status['nodes']:
        if n['type'].endswith('httpRequest'):n['credentials']={'httpHeaderAuth':binding}
    workflows.append(status)
    before=counts()
    call([*docker,'exec','-i',container,'node','-e',"require('fs').writeFileSync('/home/node/.n8n/phase-11-import.json',require('fs').readFileSync(0))"],input=json.dumps(workflows))
    call([*docker,'exec',container,'n8n','import:workflow','--input=/home/node/.n8n/phase-11-import.json'])
    call([*docker,'exec',container,'n8n','publish:workflow','--id='+helper])
    try:
        call([*compose,'restart','invoice-api','n8n'])
        for _ in range(45):
            try:
                with urllib.request.urlopen('http://localhost:5678/healthz',timeout=2) as r:assert r.status==200
                with urllib.request.urlopen('http://localhost:8080/health',timeout=2) as r:assert r.status==200
                break
            except Exception:time.sleep(1)
        else:raise RuntimeError('Services did not recover after restart.')
        results=[]
        for kind in ('Status','Intake','Reports'):
            id=prefix+kind
            result=call([*compose,'run','--rm','--no-deps','--entrypoint','n8n','n8n','execute','--id='+id,'--rawOutput'],timeout=180,check=False)
            (private/('phase-11-'+kind.lower()+'.log')).write_text(result.stdout+'\n'+result.stderr)
            execution=decode_execution(result.stdout);data=execution['data']['resultData']
            assert execution['status']=='success',kind+' failed; inspect private logs.'
            last=data['lastNodeExecuted'];output=data['runData'][last][-1]['data']['main'][0][0]['json']
            summary={'case':'fresh-'+kind.lower(),'status':'passed'}
            if kind=='Status':
                body=output.get('body',output)
                assert body['code']=='STATUS_READY'
                summary['saved_credential_authenticated']=True
            elif kind=='Intake':
                assert output['request_ok'] is True and output['new_extraction_budget']==0
                assert output['poll']['new_deliveries']==0 and output['deliveries']==5
                assert output['counts']=={'held':2,'unsupported':1,'verified':2}
                summary.update(deliveries=5,counts=output['counts'],duplicate_deliveries=output['duplicate_deliveries'],poll=output['poll'])
            else:
                assert output['status']=='completed' and output['refreshed_months']==2
                summary['verified_reports']=output['verified_reports']
            results.append(summary);print(json.dumps(summary),flush=True)
        after=counts();assert after==before,'Saved invoice/extraction counters changed.'
        for w in workflows:
            saved=export(w['id'],'phase-11-checked.json')
            assert bool(saved['active'])==(w['id']==helper)
            assert all(n.get('disabled') is True for n in saved['nodes'] if n['type'].endswith('scheduleTrigger'))
            for n in saved['nodes']:
                if n['type'].endswith('code'):
                    assert n['parameters']['language']=='javaScript'
                    assert n['parameters']['jsCode']==next(x for x in w['nodes'] if x['id']==n['id'])['parameters']['jsCode']
        return {'phase':11,'verified_at':datetime.now(timezone.utc).isoformat(),'fresh_import':'passed','restart':'passed',
            'existing_saved_credential_rebound':True,'credential_values_exported':False,'cases':results,'before':before,'after':after,
            'new_model_calls':0,'new_bill_creation_intents':0,'schedules_enabled':False,
            'scope':'New IDs in the existing local n8n instance; not a new owner-account installation. Synthetic Gmail/Sheets acceptance uses existing provider grants.',
            'temporary_acceptance_workflows_unpublished_after_test':True}
    finally:
        call([*docker,'exec',container,'n8n','unpublish:workflow','--id='+helper])
        call([*compose,'restart','n8n'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--context',default='');p.add_argument('--output',type=Path)
    a=p.parse_args();result=verify(a.context)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'fresh_import':result['fresh_import'],'restart':result['restart'],'new_model_calls':0,'schedules_enabled':False},indent=2))
