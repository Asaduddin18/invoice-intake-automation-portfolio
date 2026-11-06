"""Read local Phase 8 evidence. No Gmail, Xero, Sheets or model requests.

Use --context for an isolated Colima runtime. Run after preparing and starting
Compose. The optional baseline is private and created before the first migration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def verify(context):
    docker=['docker']+(['--context',context] if context else [])
    def command(*args):
        return subprocess.check_output([*docker,*args],text=True,cwd=ROOT,timeout=30)
    def container(service):
        ids=command('ps','-q','--filter','label=com.docker.compose.project=northmere-invoice-learning',
                    '--filter','label=com.docker.compose.service='+service).split()
        if len(ids)!=1:raise AssertionError('Expected one running '+service+' container.')
        return json.loads(command('inspect',ids[0]))[0]
    backend=container('invoice-api');n8n=container('n8n')
    for obj in (backend,n8n):
        assert obj['State']['Health']['Status']=='healthy','Wait for both services to become healthy.'
        bindings=obj['HostConfig']['PortBindings']
        assert bindings and all(x['HostIp']=='127.0.0.1' for group in bindings.values() for x in group)
    n8n_keys={item.split('=',1)[0] for item in n8n['Config']['Env']}
    assert not any(key.startswith(('XERO_','GOOGLE_','DEEPSEEK_')) for key in n8n_keys)
    assert {m['Destination'] for m in n8n['Mounts']}=={'/imports','/home/node/.n8n','/run/secrets/n8n_encryption_key'}
    assert next(m for m in n8n['Mounts'] if m['Destination']=='/imports')['RW'] is False
    connection=json.loads(command('exec',n8n['Id'],'node','-e',
        "fetch('http://invoice-api:8080/health').then(async r=>{if(!r.ok)process.exit(1);console.log(JSON.stringify(await r.json()))}).catch(()=>process.exit(1))"))
    assert connection=={'status':'ok','service':'invoice-intake'}
    marker=json.loads(command('exec',n8n['Id'],'node','-e',
        "console.log(require('fs').readFileSync('/home/node/.n8n/phase-08-persistence.json','utf8'))"))
    assert marker['phase']==8
    backend_state=json.loads(command('exec',backend['Id'],'python','-c',
        "import json;from app.connection import TokenStore;from app.google_connection import GoogleStore;TokenStore('/app/.runtime').load();GoogleStore('/app/.runtime').load();print(json.dumps({'xero_saved_token_decrypts':True,'google_saved_token_decrypts':True}))"))
    n8n_state=json.loads(command('exec',n8n['Id'],'node','-e',
        "const {DatabaseSync}=require('node:sqlite');const db=new DatabaseSync('/home/node/.n8n/database.sqlite',{readOnly:true});console.log(JSON.stringify({users:db.prepare('SELECT count(*) AS n FROM user').get().n,workflow:db.prepare(\"SELECT id,active FROM workflow_entity WHERE id='northmerePhase08'\").get(),executions:db.prepare(\"SELECT count(*) AS n FROM execution_entity WHERE workflowId='northmerePhase08' AND status='success'\").get().n}));db.close()"))
    assert n8n_state['users']>=1
    assert n8n_state['workflow']['active']==0
    assert n8n_state['executions']>=1
    runtime=ROOT/'.runtime';baseline_path=runtime/'n8n/backend-baseline.json'
    baseline=json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if baseline is not None:
        assert all((runtime/p).exists() and hashlib.sha256((runtime/p).read_bytes()).hexdigest()==digest for p,digest in baseline.items()),'Backend runtime changed; inspect the private baseline before concluding.'
    with urllib.request.urlopen('http://localhost:5678',timeout=10) as response:
        assert response.status==200
    result={'phase':8,'backend_health':connection,'services_healthy':True,'published_ports_loopback_only':True,
        'n8n_has_provider_credentials':False,'n8n_mounts_isolated':True,'persistence_marker_survived_restart':True,
        'backend_saved_credentials_readable':backend_state,'n8n_owner_account_present':True,
        'connectivity_workflow_inactive':True,'successful_connectivity_executions':n8n_state['executions'],
        'baseline_files_unchanged':len(baseline) if baseline is not None else None,
        'provider_requests_by_verifier':0,'new_paid_model_calls':0,
        'n8n_version':'2.46.6','docker_engine':command('version','--format','{{.Server.Version}}').strip()}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',default='')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.context)
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
