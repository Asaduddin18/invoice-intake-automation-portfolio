"""Owner-only local evidence; credentials and ground truth never enter a run."""
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.extraction import ExtractionError, PROMPT, PROMPT_VERSION, extract_document

class ExtractionRuns:
    def __init__(self, directory):
        self.directory = Path(directory) / 'extractions'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def folder(self, run_id):
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError('Invalid extraction identifier.')
        return self.directory / run_id

    def save(self, record):
        folder = self.folder(record['id'])
        folder.mkdir(mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, folder / 'result.json')
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def load(self, run_id):
        return json.loads((self.folder(run_id) / 'result.json').read_text())

    def source(self, run_id):
        record = self.load(run_id)
        data = (self.folder(run_id) / 'source.pdf').read_bytes()
        if hashlib.sha256(data).hexdigest() != record['source_sha256']:
            raise ExtractionError('Stored PDF hash mismatch; processing held.')
        return data

    def recent(self):
        paths = sorted(self.directory.glob('*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        return [json.loads(p.read_text()) for p in paths[:30]]

    def cached(self, digest, prompt_hash, model):
        for path in sorted(self.directory.glob('*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            record = json.loads(path.read_text())
            # Operator practice copies are deliberately altered snapshots, never model cache entries.
            if record.get('fixture_source_run') or record.get('fixture_note'):
                continue
            if record.get('source_sha256') == digest and record.get('prompt_sha256') == prompt_hash and record.get('requested_model') == model:
                return record
        return None

    async def run(self, data, filename, api_key, *, model='deepseek-flash', client=None, compare_vision=False):
        run_id = uuid.uuid4().hex
        record = {'id': run_id, 'filename': Path(filename).name,
            'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'processing',
            'source_sha256': hashlib.sha256(data).hexdigest(), 'attempts': [], 'issues': [],
            'prompt_version': PROMPT_VERSION, 'prompt_sha256': hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
            'requested_model': model}
        folder = self.folder(run_id)
        folder.mkdir(mode=0o700)
        with (folder / 'source.pdf').open('xb') as source:
            os.chmod(source.name, 0o600)
            source.write(data); source.flush(); os.fsync(source.fileno())
        self.save(record)  # A crash leaves visible processing evidence; never replay automatically.
        result = await extract_document(data, api_key, model=model, client=client, compare_vision=compare_vision)
        record.update(result)
        self.save(record)
        return record
