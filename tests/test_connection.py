import asyncio
import json
import time
from urllib.parse import parse_qs
import httpx
from app.connection import TokenStore, XeroConnection, ConnectionError
from app.main import Settings


def record():
    return dict(access_token='old-access',refresh_token='old-refresh',expires_at=time.time()-10,tenant_id='uk-id',tenant_name='Demo Company (UK)')

def test_storage_encrypted_private_and_restart(tmp_path):
    store=TokenStore(tmp_path/'storage');data=record();store.save(data)
    assert b'old-access' not in store.path.read_bytes()
    assert b'old-refresh' not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.keypath.stat().st_mode & 0o777 == 0o600
    assert TokenStore(store.directory).load()==data

def test_refresh_rotation_persists_before_next_request(tmp_path):
    store=TokenStore(tmp_path);store.save(record());calls=[]
    def handler(r):
        calls.append(r.url.path)
        if r.url.path.endswith('/token'):
            assert parse_qs(r.content.decode())['refresh_token']==['old-refresh']
            return httpx.Response(200,json=dict(access_token='new-access',refresh_token='new-refresh',expires_in=1800))
        assert store.load()['refresh_token']=='new-refresh'
        assert r.headers['Authorization']=='Bearer new-access'
        return httpx.Response(200,json=[dict(tenantId='uk-id')])
    c=XeroConnection(store,Settings('id','secret'),httpx.MockTransport(handler))
    assert asyncio.run(c.company())['tenant_name']=='Demo Company (UK)'
    assert store.load()['refresh_token']=='new-refresh'
    assert len(calls)==2

def test_parallel_reads_only_refresh_once(tmp_path):
    store=TokenStore(tmp_path);store.save(record());refreshes=[]
    def handler(r):
        if r.url.path.endswith('/token'):
            refreshes.append(1);return httpx.Response(200,json=dict(access_token='new',refresh_token='rotated',expires_in=1800))
        return httpx.Response(200,json=[dict(tenantId='uk-id')])
    c=XeroConnection(store,Settings('id','secret'),httpx.MockTransport(handler))
    async def run():return await asyncio.gather(c.company(),c.company())
    asyncio.run(run());assert len(refreshes)==1

def test_revoked_refresh_requires_reconnect_without_leak(tmp_path):
    store=TokenStore(tmp_path);store.save(record())
    c=XeroConnection(store,Settings('id','secret'),httpx.MockTransport(lambda r:httpx.Response(400,text='sensitive-upstream')))
    try:asyncio.run(c.company());assert False
    except ConnectionError as exc:
        assert 'Reconnect' in str(exc) and 'sensitive' not in str(exc)
    assert store.load()['refresh_token']=='old-refresh'

def test_corrupt_storage_is_actionable(tmp_path):
    store=TokenStore(tmp_path);store.save(record());store.path.write_bytes(b'broken')
    try:store.load();assert False
    except ConnectionError as exc:assert 'reconnect' in str(exc)

def test_wrong_tenant_not_saved(tmp_path):
    store=TokenStore(tmp_path);c=XeroConnection(store,Settings('id','secret'))
    try:asyncio.run(c.save_authorization({},[dict(tenantName='Real company',tenantType='ORGANISATION',tenantId='real')]));assert False
    except ConnectionError:pass
    assert not store.path.exists()
