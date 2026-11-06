"""Run the exact JavaScript embedded into workflow nodes, without providers."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_phase10_workflow_logic_and_export_safety():
    node=os.getenv('NODE_BINARY') or shutil.which('node')
    if not node:pytest.skip('Node is required for workflow logic; CI installs it.')
    subprocess.run([node,str(ROOT/'tests/phase10_workflows.cjs')],cwd=ROOT,check=True,capture_output=True,text=True)


def test_workflow_generator_recreates_checked_in_exports(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('n8n_build',ROOT/'scripts/build_n8n_workflows.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=module.OUT
    module.OUT=tmp_path
    shutil.copytree(original/'logic',tmp_path/'logic')
    module.build()
    for name in ('errors','intake','reports'):
        file='phase-10-'+name+'.json'
        assert (tmp_path/file).read_bytes()==(original/file).read_bytes()
