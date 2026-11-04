"""Release checks for inspectable, synthetic portfolio assets."""
import json
from pathlib import Path
import io
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]


def test_full_dataset_is_searchable_and_duplicate_families_do_not_leak():
    records={}
    pdfs=list((ROOT/'dataset').glob('*/*.pdf'))
    assert len(pdfs)==50
    for path in pdfs:
        truth=json.loads(path.with_suffix('.json').read_text())
        records[truth['id']]=truth
        reader=PdfReader(io.BytesIO(path.read_bytes()))
        text='\n'.join(page.extract_text() or '' for page in reader.pages)
        assert 'SYNTHETIC DEMO INVOICE' in text
        assert truth['currency']=='GBP'
        assert truth['filename']==path.name
    for row in records.values():
        if row.get('duplicate_of'):
            assert row['split']==records[row['duplicate_of']]['split']


def test_public_replay_has_explicit_limits_and_five_scenarios():
    data=json.loads((ROOT/'site/data.json').read_text())
    assert len(data)==5
    assert [r['outcome'] for r in data]==['verified','duplicate','held','held','unsupported']
    assert data[2]['number'] is None
    assert data[3]['total']=='553.13'  # Preserve the deliberately incorrect printed value.
    for row in data:
        if row['source']:assert (ROOT/'site'/row['source']).is_file()
    html=(ROOT/'site/index.html').read_text()
    assert 'Illustrated replay' in html and 'makes no API calls' in html


def test_public_metrics_match_the_documented_sample():
    metrics=json.loads((ROOT/'artifacts/public/metrics.json').read_text())
    assert metrics['evaluated_distinct_invoices']==20
    assert metrics['matched_leaf_fields']==metrics['expected_leaf_fields']==836
    assert metrics['initial_valid_documents_held']==1
    assert metrics['live_acceptance']['counted_total_gbp']=='95.16'


def test_replay_steps_and_reset_without_external_network():
    import os,shutil,subprocess
    import pytest
    node=os.getenv('NODE_BINARY') or shutil.which('node')
    if not node:pytest.skip('Node is required for the standalone replay check; CI installs it.')
    subprocess.run([node,str(ROOT/'tests/replay_checks.cjs')],check=True,capture_output=True,text=True)
