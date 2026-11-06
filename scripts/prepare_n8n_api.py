"""Prepare a dedicated local API token. Never print it or overwrite an existing one."""
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values,set_key


def prepare(root):
    root=Path(root);env=root/'.env'
    if not env.exists():raise SystemExit('Create the private .env from .env.example first.')
    existing=dotenv_values(env).get('INVOICE_N8N_API_TOKEN')
    if existing and (len(existing.strip())<32 or len(existing.strip())>256):
        raise SystemExit('Existing API token has invalid length. Resolve it explicitly before changing credentials.')
    token=existing.strip() if existing else secrets.token_hex(32)
    if not existing:set_key(str(env),'INVOICE_N8N_API_TOKEN',token)
    os.chmod(env,0o600)
    directory=root/'.runtime'/'n8n';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/'api-token.txt'
    # Private copy for the owner's credential-setup exercise. Never mounted in n8n.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:stream.write('Bearer '+token)
    os.chmod(path,0o600)
    return path


if __name__=='__main__':
    prepare(Path(__file__).resolve().parents[1])
    print('Dedicated token ready in private .env and .runtime/n8n/api-token.txt. No token printed.')
