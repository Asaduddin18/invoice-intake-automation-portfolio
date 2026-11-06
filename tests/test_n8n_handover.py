"""Public walkthrough behavior and final handover package checks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_public_n8n_walkthrough_steps_without_network():
    node=os.getenv('NODE_BINARY') or shutil.which('node')
    if not node:pytest.skip('Node required; installed in CI.')
    subprocess.run([node,str(ROOT/'tests/n8n_replay_checks.cjs')],check=True,capture_output=True,text=True)


def test_final_n8n_handover_assets_and_scope():
    for file in ('site/n8n.html','site/n8n.css','site/n8n-demo.js','site/images/n8n-architecture.svg','docs/n8n-handover.md','scripts/verify_n8n_handoff.py'):
        assert (ROOT/file).is_file()
    html=(ROOT/'site/n8n.html').read_text()
    assert 'makes no API calls' in html and 'schedules remain disabled' in html
    assert 'type="password"' not in html and 'type="email"' not in html
    evidence=json.loads((ROOT/'artifacts/public/n8n-release.json').read_text())
    assert evidence['acceptance']['new_model_calls']==0
    assert evidence['acceptance']['new_bill_creation_intents']==0
    assert evidence['acceptance']['schedules_enabled'] is False
    assert evidence['acceptance']['before']==evidence['acceptance']['after']
    assert evidence['operator_attestation']=='Not claimed; independent user walkthrough is a learning exercise.'
