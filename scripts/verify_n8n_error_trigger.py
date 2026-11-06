"""Verify a synthetic automatic failure without enabling invoice schedules."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
PROBE='northmereP10AutomaticProbe'


def verify(context):
    docker=['docker']+(['--context',context] if context else [])
    def call(*args,input=None):
        return subprocess.run([*docker,*args],input=input,text=True,capture_output=True,check=True,timeout=90).stdout
    container='northmere-invoice-learning-n8n-1'
    workflow={'id':PROBE,'name':'Phase 10 test - automatic failure','active':False,'nodes':[
        {'id':'webhook','name':'Synthetic local request','type':'n8n-nodes-base.webhook','typeVersion':2,
         'position':[0,0],'webhookId':'northmere-phase10-proof','parameters':{'httpMethod':'POST','path':'northmere-phase10-proof','responseMode':'onReceived','options':{}}},
        {'id':'stop','name':'Deliberate synthetic failure','type':'n8n-nodes-base.stopAndError','typeVersion':1,
         'position':[250,0],'parameters':{'errorType':'errorMessage','errorMessage':'Synthetic Phase 10 automatic failure'}}],
        'connections':{'Synthetic local request':{'main':[[{'node':'Deliberate synthetic failure','type':'main','index':0}]]}},
        'settings':{'errorWorkflow':'northmereP10Errors','timezone':'Asia/Kolkata'}}
    call('exec','-i',container,'node','-e',"require('fs').writeFileSync('/home/node/.n8n/phase-10-auto-probe.json',require('fs').readFileSync(0))",input=json.dumps([workflow]))
    call('exec',container,'n8n','import:workflow','--input=/home/node/.n8n/phase-10-auto-probe.json')
    # The pinned n8n version requires a published called workflow.
    call('exec',container,'n8n','publish:workflow','--id=northmereP10Errors')
    call('exec',container,'n8n','publish:workflow','--id='+PROBE)
    def restart():
        call('restart',container)
        for _ in range(45):
            try:
                if urllib.request.urlopen('http://127.0.0.1:5678/healthz',timeout=2).status==200:return
            except Exception:pass
            time.sleep(1)
        raise RuntimeError('n8n did not recover after restart.')
    try:
        restart()
        baseline_query="""
const sqlite=require(require.resolve('sqlite3',{paths:['/usr/local/lib/node_modules/n8n']}));
const db=new sqlite.Database('/home/node/.n8n/database.sqlite',sqlite.OPEN_READONLY);
db.get('SELECT MAX(id) AS latest FROM execution_entity',(error,row)=>{if(error)throw error;console.log(JSON.stringify(row.latest||0));db.close();});
"""
        baseline=int(json.loads(call('exec',container,'node','-e',baseline_query)))
        req=urllib.request.Request('http://127.0.0.1:5678/webhook/northmere-phase10-proof',data=b'{}',headers={'Content-Type':'application/json'})
        # Health can turn green before production webhooks finish registering.
        for attempt in range(30):
            try:
                with urllib.request.urlopen(req,timeout=10) as response:assert response.status==200
                break
            except urllib.error.HTTPError as error:
                if error.code!=404 or attempt==29:raise
                time.sleep(1)
        # Read only workflow state and execution metadata for this synthetic probe.
        query="""
const sqlite=require(require.resolve('sqlite3',{paths:['/usr/local/lib/node_modules/n8n']}));
const db=new sqlite.Database('/home/node/.n8n/database.sqlite',sqlite.OPEN_READONLY);
db.all('SELECT workflowId,mode,status FROM execution_entity WHERE id > ? AND workflowId IN (?,?) ORDER BY id DESC LIMIT 12',[Number(process.argv[1]),'northmereP10AutomaticProbe','northmereP10Errors'],(error,rows)=>{if(error)throw error;console.log(JSON.stringify(rows));db.close();});
"""
        for _ in range(20):
            rows=json.loads(call('exec',container,'node','-e',query,str(baseline)))
            if any(r['workflowId']=='northmereP10Errors' and r['mode']=='error' and r['status']=='success' for r in rows):break
            time.sleep(1)
        assert any(r['workflowId']==PROBE and r['mode']=='webhook' and r['status']=='error' for r in rows),rows
        assert any(r['workflowId']=='northmereP10Errors' and r['mode']=='error' and r['status']=='success' for r in rows),rows
        return {'automatic_probe':'passed','trigger':'local synthetic webhook','original_execution':'error','error_handler_execution':'success',
                'provider_requests':0,'invoice_schedules_enabled':False,'temporary_probe_unpublished_after_test':True}
    finally:
        call('exec',container,'n8n','unpublish:workflow','--id='+PROBE)
        restart()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--context',default='');p.add_argument('--output',type=Path)
    a=p.parse_args();result=verify(a.context)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
