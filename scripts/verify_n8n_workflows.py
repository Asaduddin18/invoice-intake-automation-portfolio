"""Run real n8n executions against live demo APIs and a private synthetic fixture.

No schedules are activated. Live intake uses cached existing documents only.
The fixture receives no credentials and cannot access backend runtime/provider keys.
"""
import argparse
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PROJECT='northmere-invoice-learning'
FIXTURE='northmere-phase10-fixture'


def decode_execution(text):
    decoder=json.JSONDecoder()
    for pos,char in enumerate(text):
        if char!='{':continue
        try:value,_=decoder.raw_decode(text[pos:])
        except ValueError:continue
        if isinstance(value,dict) and isinstance(value.get('data'),dict) and 'resultData' in value['data']:return value
    raise RuntimeError('Execution output could not be decoded. Inspect the private verification log.')


def verify(context):
    docker=['docker']+(['--context',context] if context else [])
    compose=['docker-compose']+(['--context',context] if context else [])
    directory=ROOT/'.runtime/n8n';directory.mkdir(exist_ok=True)
    def call(args,input=None,timeout=60,check=True):
        return subprocess.run(args,input=input,capture_output=True,text=True,cwd=ROOT,timeout=timeout,check=check)
    ids=call([*docker,'ps','-q','--filter','label=com.docker.compose.project='+PROJECT,'--filter','label=com.docker.compose.service=n8n']).stdout.split()
    if len(ids)!=1:raise RuntimeError('Start one n8n service before verification.')
    container=ids[0]
    def state_counts():
        with sqlite3.connect('file:'+str(ROOT/'.runtime/ledger/invoices.sqlite3')+'?mode=ro',uri=True) as db:
            result={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('invoices','attempts')}
        result['extractions']=len(list((ROOT/'.runtime/extractions').glob('*/result.json')))
        return result
    def execute(id,label,expected,terminal):
        result=call([*compose,'run','--rm','--no-deps','--entrypoint','n8n','n8n','execute','--id='+id,'--rawOutput'],timeout=180,check=False)
        (directory/(label+'.log')).write_text(result.stdout+'\n'+result.stderr)
        parsed=decode_execution(result.stdout);data=parsed['data']['resultData'];last=data.get('lastNodeExecuted')
        if parsed.get('status')!=expected or last!=terminal:
            raise RuntimeError('Unexpected execution outcome for '+label+'. Inspect the private log.')
        outputs=data.get('runData',{}).get(last,[{}])[-1].get('data',{}).get('main',[])
        safe={'case':label,'status':expected,'last_node':last,'outputs':outputs}
        if expected=='error':
            safe['failure_summaries']=[entry['data']['main'][0][0]['json'] for entry in data.get('runData',{}).get('Record safe failure',[]) if entry.get('data')]
            if not safe['failure_summaries']:raise RuntimeError('Missing manual failure summary for '+label)
        if 'do-not-copy-fixture' in json.dumps(safe):raise RuntimeError('Unsafe fixture field reached final output.')
        print(json.dumps({'case':label,'status':expected,'last_node':last}),flush=True)
        return safe
    before=state_counts();results=[]
    results.append(execute('northmereP10Intake','live-intake','success','Review exceptions in invoice app'))
    results.append(execute('northmereP10Reports','live-reporting','success','Reporting complete'))
    if state_counts()!=before:raise RuntimeError('Live workflow changed accounting or extraction counters.')
    # Isolated mock server has no host port, credentials or backend storage.
    fixture_file=ROOT/'tests/fixtures/n8n_phase10_server.py'
    call([*docker,'run','--detach','--name',FIXTURE,'--network',PROJECT+'_invoice-learning',
        '--mount','type=bind,source='+str(fixture_file)+',target=/fixture.py,readonly',
        'python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8','python','/fixture.py'])
    def probe(kind,scenario,id,unavailable=False):
        w=json.loads((ROOT/'deploy/n8n/phase-10'/('phase-10-'+kind+'.json')).read_text());w['id']=id;w['name']='Phase 10 test - '+scenario
        for node in w['nodes']:
            if node['type'].endswith('httpRequest'):
                suffix=node['parameters']['url'].rsplit('/',1)[1]
                node['parameters']['url']='http://invoice-api:1/unavailable' if unavailable else 'http://'+FIXTURE+':8080/'+scenario+'/'+suffix
                node['parameters'].pop('authentication',None);node['parameters'].pop('genericAuthType',None)
                node['parameters']['options']['timeout']=5000
                node.pop('credentials',None)
        return w
    probes=[probe('intake','clear','p10TestClear'),probe('intake','unauthorized','p10TestUnauthorized'),
        probe('intake','closed-port','p10TestClosedPort',True),probe('reports','empty','p10TestEmpty'),probe('reports','recovery','p10TestRecovery')]
    try:
        call([*docker,'exec','-i',container,'node','-e',"require('fs').writeFileSync('/home/node/.n8n/phase-10-probes.json',require('fs').readFileSync(0))"],input=json.dumps(probes))
        call([*docker,'exec',container,'n8n','import:workflow','--input=/home/node/.n8n/phase-10-probes.json'])
        results.append(execute('p10TestClear','mock-clear-intake','success','Intake complete - no pending exceptions'))
        results.append(execute('p10TestUnauthorized','mock-unauthorized','error','Stop and inspect saved state'))
        results.append(execute('p10TestClosedPort','closed-port-unavailable','error','Stop and inspect saved state'))
        results.append(execute('p10TestEmpty','mock-empty-months','success','Nothing to report'))
        results.append(execute('p10TestRecovery','mock-report-failure','error','Stop and inspect saved state'))
        results.append(execute('p10TestRecovery','mock-report-recovery','success','Reporting complete'))
        trace=json.loads(call([*docker,'exec',container,'node','-e',"fetch('http://"+FIXTURE+":8080/trace').then(async r=>console.log(JSON.stringify(await r.json())))"]).stdout)['requests']
        assert not any(row['authorization_present'] for row in trace)
        reports=[r['month'] for r in trace if r['scenario']=='recovery' and r['operation']=='report']
        assert reports==['2026-08','2026-09','2026-08','2026-09','2026-11'],reports
        assert not any(r['operation']=='report' for r in trace if r['scenario']=='empty')
        assert not any(r['operation']=='intake' for r in trace if r['scenario']=='recovery')
        assert state_counts()==before
        return {'phase':10,'verified_at':datetime.now(timezone.utc).isoformat(),'before':before,'after':state_counts(),
            'new_model_calls':0,'new_bill_creation_intents':0,'executions':results,'fixture_requests':trace,
            'schedules_activated':False,'fixture_credentials_received':False,
            'limitations':'Failure/recovery scenarios use a private synthetic API stand-in or closed port; live APIs were tested separately. Automatic Error Trigger is checked by verify_n8n_error_trigger.py.'}
    finally:
        call([*docker,'rm','-f',FIXTURE],check=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--context',default='');p.add_argument('--output',type=Path)
    a=p.parse_args();result=verify(a.context)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'verified_cases':len(result['executions']),'new_model_calls':0,'before':result['before'],'after':result['after']},indent=2))
