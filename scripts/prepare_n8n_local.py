"""Prepare the local n8n encryption key without printing or replacing secrets."""
import os
from pathlib import Path
import secrets


def prepare(root):
    directory=Path(root)/'.runtime'/'n8n'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(directory,0o700)
    key=directory/'encryption.key'
    try:
        fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        if len(key.read_text().strip())<32:
            raise SystemExit('Existing n8n key is too short. Restore the original key; do not overwrite it.')
    else:
        with os.fdopen(fd,'w') as stream:
            stream.write(secrets.token_hex(32))
            stream.flush();os.fsync(stream.fileno())
    os.chmod(key,0o600)
    return key


if __name__=='__main__':
    prepare(Path(__file__).resolve().parents[1])
    print('Local n8n encryption key ready. Existing key preserved; no secret printed.')
