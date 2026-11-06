"""Import inactive Phase 10 workflows, reusing the saved Phase 9 credential binding.

Uses official workflow export/import commands. No credential values are exported.
Public JSON remains credential-free. Existing Phase 10 workflows are never overwritten
unless --replace is explicitly requested for a deliberate local update.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def install(context,replace=False):
    docker=['docker']+(['--context',context] if context else [])
    def call(*args,input=None):
        return subprocess.run([*docker,*args],input=input,capture_output=True,text=True,check=True,timeout=60).stdout
    ids=call('ps','-q','--filter','label=com.docker.compose.project=northmere-invoice-learning','--filter','label=com.docker.compose.service=n8n').split()
    if len(ids)!=1:raise SystemExit('Start exactly one local n8n service first.')
    container=ids[0]
    call('exec',container,'n8n','export:workflow','--id=northmerePhase09','--output=/home/node/.n8n/phase-10-reference.json')
    source=json.loads(call('exec',container,'node','-e',"console.log(require('fs').readFileSync('/home/node/.n8n/phase-10-reference.json','utf8'))"))[0]
    bindings=[node.get('credentials',{}).get('httpHeaderAuth') for node in source['nodes'] if node['type'].endswith('httpRequest')]
    binding=next((b for b in bindings if b and b.get('id')),None)
    if not binding:raise SystemExit('Bind and save the working Phase 9 Header Auth credential first.')
    existing=call('exec',container,'n8n','list:workflow','--onlyId').split()
    workflows=[]
    for name in ('errors','intake','reports'):
        w=json.loads((ROOT/'deploy/n8n/phase-10'/('phase-10-'+name+'.json')).read_text())
        if w['id'] in existing and not replace:raise SystemExit('Phase 10 already exists. Inspect it before deliberately using --replace.')
        for node in w['nodes']:
            if node['type'].endswith('httpRequest'):node['credentials']={'httpHeaderAuth':binding}
        workflows.append(w)
    call('exec','-i',container,'node','-e',"require('fs').writeFileSync('/home/node/.n8n/phase-10-import.json',require('fs').readFileSync(0))",input=json.dumps(workflows))
    call('exec',container,'n8n','import:workflow','--input=/home/node/.n8n/phase-10-import.json')
    # Called sub-workflows must be published in the pinned n8n version. This
    # helper has no timer or provider request. Intake and reporting stay inactive.
    call('exec',container,'n8n','publish:workflow','--id=northmereP10Errors')
    return {'imported':[w['name'] for w in workflows],'intake_and_reporting_active':False,
        'failure_helper_published':True,'restart_n8n_required':True,
        'provider_credentials_exported':False,'binding':'existing Phase 9 Header Auth credential'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--context',default='');p.add_argument('--replace',action='store_true')
    a=p.parse_args();print(json.dumps(install(a.context,a.replace),indent=2))
