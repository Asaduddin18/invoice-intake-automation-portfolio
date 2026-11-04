"""Google OAuth for one explicitly configured demo mailbox. Never log credentials."""
import asyncio
import os
import time
from pathlib import Path
import httpx
from cryptography.fernet import InvalidToken
import json

from app.connection import TokenStore, ConnectionError

SCOPES = ('https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/drive.file')
REDIRECT = 'http://localhost:8080/oauth/google/callback'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'

class GoogleError(ConnectionError):
    pass

class GoogleStore(TokenStore):
    def __init__(self, directory):
        super().__init__(Path(directory) / 'google')

    def load(self):
        try:
            if not self.path.exists() or not self.keypath.exists():
                raise GoogleError('Connect the demo Gmail account first.')
            data = json.loads(self._cipher().decrypt(self.path.read_bytes()))
            if not all(data.get(k) for k in ('access_token','refresh_token','expires_at','mailbox')):
                raise ValueError()
            return data
        except (InvalidToken, ValueError, TypeError, OSError):
            raise GoogleError('Google connection is unreadable. Reconnect the demo account.') from None

class GoogleConnection:
    def __init__(self, directory, *, client_id=None, client_secret=None, mailbox=None, transport=None):
        self.store = GoogleStore(directory)
        self.client_id = client_id if client_id is not None else os.getenv('GOOGLE_CLIENT_ID','')
        self.client_secret = client_secret if client_secret is not None else os.getenv('GOOGLE_CLIENT_SECRET','')
        self.mailbox = (mailbox if mailbox is not None else os.getenv('GOOGLE_MAILBOX','')).strip().lower()
        self.transport = transport
        self.lock = asyncio.Lock()

    def configured(self):
        if not self.client_id or not self.client_secret or not self.mailbox:
            raise GoogleError('Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_MAILBOX in local .env, then restart.')
        if os.getenv('GOOGLE_REDIRECT_URI', REDIRECT) != REDIRECT:
            raise GoogleError('Google redirect must match the registered localhost callback.')

    async def token(self, data):
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=25, follow_redirects=False) as client:
                response = await client.post(TOKEN_URL, data={**data,'client_id':self.client_id,'client_secret':self.client_secret})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise GoogleError('Google token exchange failed. Reconnect if access expired or was revoked.') from None

    async def authorize(self, code, verifier):
        self.configured()
        tokens = await self.token({'grant_type':'authorization_code','code':code,
                                  'redirect_uri':REDIRECT,'code_verifier':verifier})
        if not set(SCOPES).issubset(set(tokens.get('scope','').split())):
            raise GoogleError('Both Gmail read-only and app-file access must be granted.')
        if not tokens.get('refresh_token'):
            raise GoogleError('Offline access was not returned. Reconnect and approve offline access.')
        profile = await self._call('GET', GMAIL+'/profile', tokens['access_token'])
        actual = profile.get('emailAddress','').lower()
        if actual != self.mailbox:
            raise GoogleError('Wrong mailbox selected. Connect the configured synthetic-data Gmail account.')
        async with self.lock:
            self.store.save({'access_token':tokens['access_token'],'refresh_token':tokens['refresh_token'],
                'expires_at':time.time()+int(tokens['expires_in']),'mailbox':actual})
        return actual

    async def _call(self, method, url, access, **kwargs):
        # Fixed Google endpoints only. No caller-supplied domains or redirect following.
        if not (url == SHEETS or url.startswith(SHEETS+'/') or url.startswith(GMAIL+'/')):
            raise GoogleError('Unsupported Google endpoint.')
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=30, follow_redirects=False) as client:
                async with client.stream(method,url,headers={'Authorization':'Bearer '+access},**kwargs) as response:
                    if response.status_code == 401:
                        raise GoogleError('Google access expired. Reconnect before retrying.')
                    if response.status_code >= 400:
                        raise GoogleError('Google request failed ('+str(response.status_code)+'). Check enabled APIs, permissions and quota. No accounting action was retried.')
                    chunks=[]; size=0
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>16_000_000:raise GoogleError('Google response exceeded the local size limit.')
                        chunks.append(chunk)
                    return json.loads(b''.join(chunks))
        except (httpx.HTTPError, ValueError):
            raise GoogleError('Google request did not finish. Inspect the saved operation before retrying.') from None

    async def request(self, method, url, **kwargs):
        # Single worker. Refresh is serialized; no automatic retry of API mutations.
        async with self.lock:
            saved=self.store.load()
            if saved['mailbox'] != self.mailbox:
                raise GoogleError('Saved Google account differs from the configured mailbox. Reconnect.')
            if saved['expires_at'] <= time.time()+60:
                self.configured()
                tokens=await self.token({'grant_type':'refresh_token','refresh_token':saved['refresh_token']})
                saved.update(access_token=tokens['access_token'],expires_at=time.time()+int(tokens['expires_in']))
                if tokens.get('refresh_token'):saved['refresh_token']=tokens['refresh_token']
                self.store.save(saved)
            return await self._call(method,url,saved['access_token'],**kwargs)
