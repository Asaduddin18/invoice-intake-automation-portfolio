"""Produce a reviewed public snapshot, excluding private development artifacts/history.

The destination must not exist. No provider requests or secret values are printed.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDERS=('app','tests','fixtures','dataset','docs','site','.github','deploy')
FILES=('.env.example','.gitignore','.gitattributes','README.md','SECURITY.md','requirements.txt','requirements.lock.txt','Dockerfile','.dockerignore','compose.yaml')
SCRIPTS=('build_portfolio_demo.py','export_portfolio.py','verify_google_demo.py','seed_review_demo.py','prepare_n8n_local.py','verify_n8n_environment.py','prepare_n8n_api.py','verify_n8n_api.py','build_n8n_workflows.py','install_n8n_workflows.py','verify_n8n_workflows.py','verify_n8n_error_trigger.py','verify_n8n_handoff.py')
PUBLIC_ARTIFACTS=('public/metrics.json','public/evaluation.json','public/release-verification.json','deepseek-prompts.md','public/n8n-release.json')


def export(destination):
    destination=Path(destination)
    if destination.exists():raise SystemExit('Destination already exists. Choose a new export directory.')
    destination.mkdir(parents=True)
    for name in FOLDERS:
        shutil.copytree(ROOT/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
    # Phase 7 private publication reports are retained in the development repository.
    for path in (destination/'docs').glob('phase-07*'):path.unlink()
    for name in FILES:shutil.copy2(ROOT/name,destination/name)
    for name in SCRIPTS:
        (destination/'scripts').mkdir(exist_ok=True)
        shutil.copy2(ROOT/'scripts'/name,destination/'scripts'/name)
    for name in PUBLIC_ARTIFACTS:
        path=destination/'artifacts'/name;path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'artifacts'/name,path)
    manifest=[];violations=[]
    for path in sorted(destination.rglob('*')):
        if not path.is_file():continue
        rel=path.relative_to(destination).as_posix();blob=path.read_bytes()
        if path.suffix not in ('.png','.jpg','.pdf'):
            text=blob.decode(errors='replace')
            for pattern in (r'/'+r'Users/[^\s]+',r'mohdasad\d*@gmail\.com',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+'):
                if re.search(pattern,text):violations.append(rel)
        if any(part in ('.git','.runtime','.venv') for part in path.parts) or path.name=='.env':violations.append(rel)
        manifest.append({'path':rel,'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob)})
    if violations:raise SystemExit('Public export requires review: '+', '.join(sorted(set(violations))))
    (destination/'artifacts/public/export-manifest.json').write_text(json.dumps({'files':manifest,'account_linked_development_artifacts_excluded':True},indent=2)+'\n')
    print(json.dumps({'files':len(manifest),'pdf_fixtures':len(list((destination/'dataset').glob('*/*.pdf'))),'private_path_checks':'passed'}))
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination');args=parser.parse_args();export(args.destination)
