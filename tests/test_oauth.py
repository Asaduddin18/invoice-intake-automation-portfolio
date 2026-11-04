import time
import tempfile
from urllib.parse import urlparse,parse_qs
import httpx
from fastapi.testclient import TestClient
from app.main import create_app,Settings

def setup(handler=None):
    transport=httpx.MockTransport(handler or (lambda r: httpx.Response(500)))
    app=create_app(Settings('test-client','test-secret',storage_dir=tempfile.mkdtemp()),transport)
    return app,TestClient(app,follow_redirects=False)

def start(client):
    r=client.post('/oauth/xero/start');assert r.status_code==303
    q=parse_qs(urlparse(r.headers['location']).query)
    assert q['scope']==['accounting.settings.read accounting.contacts accounting.invoices offline_access']
    assert 'client_secret' not in q
    return q['state'][0]

def test_success_and_replay_rejected():
    def handler(r):
        if r.url.path.endswith('/token'):
            assert r.headers['Authorization'].startswith('Basic ')
            return httpx.Response(200,json={'access_token':'private-token','refresh_token':'private-refresh','expires_in':1800})
        assert r.headers['Authorization']=='Bearer private-token'
        return httpx.Response(200,json=[{'tenantName':'Demo Company (UK)','tenantType':'ORGANISATION','tenantId':'demo-id'}])
    app,c=setup(handler);s=start(c)
    r=c.get('/oauth/xero/callback',params={'state':s,'code':'private-code'})
    assert r.status_code==303 and r.headers['location']=='/oauth/xero/result'
    r=c.get(r.headers['location']);assert r.json()['demo_company_uk_connected']
    assert all(x not in r.text for x in ['private-token','private-code','test-secret'])
    assert r.headers['Cache-Control']=='no-store'
    assert c.get('/oauth/xero/callback',params={'state':s,'code':'x'}).status_code==400

def test_state_cannot_be_used_from_another_browser():
    app,c=setup();s=start(c)
    other=TestClient(app)
    assert other.get('/oauth/xero/callback',params={'state':s,'code':'x'}).status_code==400
    assert s in app.state.pending

def test_expired_state():
    app,c=setup();s=start(c);app.state.pending[s]=time.monotonic()-1
    assert c.get('/oauth/xero/callback',params={'state':s,'code':'x'}).status_code==400

def test_denial():
    app,c=setup();s=start(c)
    c.get('/oauth/xero/callback',params={'state':s,'error':'access_denied'})
    assert 'declined' in c.get('/oauth/xero/result').json()['message']

def test_upstream_failure_is_sanitized():
    app,c=setup(lambda r:httpx.Response(401,text='secret-upstream-body'));s=start(c)
    c.get('/oauth/xero/callback',params={'state':s,'code':'x'})
    r=c.get('/oauth/xero/result');assert r.json()['status']=='error'
    assert 'secret-upstream-body' not in r.text

def test_missing_configuration():
    c=TestClient(create_app(Settings('','')))
    assert c.post('/oauth/xero/start').status_code==503

def test_untrusted_origin_and_host():
    app,c=setup()
    assert c.post('/oauth/xero/start',headers={'Origin':'https://other.example'}).status_code==403
    assert c.get('/',headers={'Host':'other.example'}).status_code==400


def test_same_origin_form_post_and_referrer_policy():
    app,c=setup()
    assert c.get('/').headers['Referrer-Policy']=='same-origin'
    r=c.post('/oauth/xero/start',headers={'Origin':'http://localhost:8080'})
    assert r.status_code==303
    assert c.post('/oauth/xero/start',headers={'Origin':'null'}).status_code==403


def test_navigation_start():
    app,c=setup()
    assert c.get('/oauth/xero/start',headers={'Sec-Fetch-Site':'same-origin'}).status_code==303
    assert c.get('/oauth/xero/start',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403


def test_result_survives_refresh_and_uses_redirect_compatible_cookie():
    def handler(r):
        if r.url.path.endswith('/token'):
            return httpx.Response(200,json={'access_token':'test-access','refresh_token':'test-refresh','expires_in':1800})
        return httpx.Response(200,json=[{'tenantName':'Demo Company (UK)','tenantType':'ORGANISATION','tenantId':'demo-id'}])
    app,c=setup(handler);s=start(c)
    callback=c.get('/oauth/xero/callback',params={'state':s,'code':'test-code'})
    assert 'SameSite=lax' in callback.headers['set-cookie']
    first=c.get('/oauth/xero/result')
    second=c.get('/oauth/xero/result')
    assert first.json()==second.json()
    assert second.json()['demo_company_uk_connected']
    assert TestClient(app).get('/oauth/xero/result').status_code==400
