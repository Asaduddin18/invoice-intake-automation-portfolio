"""The health probe must never invoke providers or expose connection state."""
import httpx
from fastapi.testclient import TestClient
from app.main import Settings,create_app
from scripts.prepare_n8n_local import prepare


def test_health_has_no_provider_calls_and_keeps_browser_controls(tmp_path,monkeypatch):
    monkeypatch.delenv('INVOICE_DOCKER_NETWORK',raising=False)
    def deny_provider(request):
        raise AssertionError('Health must not invoke external providers')
    client=TestClient(create_app(Settings('test','test',storage_dir=str(tmp_path)),transport=httpx.MockTransport(deny_provider)))
    assert client.get('/health').json()=={'status':'ok','service':'invoice-intake'}
    assert client.get('/health',headers={'host':'invoice-api'}).status_code==400
    assert client.get('/health',headers={'host':'untrusted.example'}).status_code==400
    assert client.post('/mail/poll',headers={'origin':'http://untrusted.example'}).status_code==409


def test_explicit_docker_alias_does_not_allow_arbitrary_hosts(tmp_path,monkeypatch):
    monkeypatch.setenv('INVOICE_DOCKER_NETWORK','true')
    client=TestClient(create_app(Settings('test','test',storage_dir=str(tmp_path))))
    assert client.get('/health',headers={'host':'invoice-api'}).status_code==200
    assert client.get('/health',headers={'host':'untrusted.example'}).status_code==400


def test_preparing_local_key_preserves_it_and_rejects_damaged_key(tmp_path):
    import pytest
    key=prepare(tmp_path);original=key.read_bytes()
    assert len(original.strip())==64
    assert prepare(tmp_path).read_bytes()==original
    assert key.stat().st_mode & 0o777==0o600
    key.write_text('broken')
    with pytest.raises(SystemExit):prepare(tmp_path)
    assert key.read_text()=='broken'
