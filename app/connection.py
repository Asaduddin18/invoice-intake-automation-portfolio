"""Single-process local connection storage and refresh. Never log token values."""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken

TOKEN_URL = 'https://identity.xero.com/connect/token'
CONNECTIONS_URL = 'https://api.xero.com/connections'

class ConnectionError(Exception):
    """Safe, actionable message suitable for the local UI."""

class TokenStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'connection.enc'
        self.keypath = self.directory / 'encryption.key'

    def _cipher(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        try:
            fd = os.open(self.keypath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
                f.flush()
                os.fsync(f.fileno())
        os.chmod(self.keypath, 0o600)
        return Fernet(self.keypath.read_bytes())

    def load(self):
        if not self.path.exists():
            raise ConnectionError('No saved connection. Connect to Demo Company (UK).')
        if not self.keypath.exists():
            raise ConnectionError('Local encryption key is missing. Restore it securely or reconnect.')
        try:
            data = json.loads(self._cipher().decrypt(self.path.read_bytes()))
            for field in ('access_token', 'refresh_token', 'expires_at', 'tenant_id', 'tenant_name'):
                if field not in data:
                    raise ValueError()
            return data
        except (InvalidToken, ValueError, TypeError, OSError):
            raise ConnectionError('Saved connection cannot be read. Restore local storage or reconnect.') from None

    def save(self, data):
        try:
            encrypted = self._cipher().encrypt(json.dumps(data).encode())
            fd, filename = tempfile.mkstemp(dir=self.directory, prefix='connection-')
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(encrypted)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(filename, self.path)
            finally:
                if os.path.exists(filename):
                    os.unlink(filename)
        except (OSError, ValueError):
            raise ConnectionError('Could not save the connection locally. Check storage permissions.') from None

class XeroConnection:
    def __init__(self, store, settings, transport=None):
        self.store, self.settings, self.transport = store, settings, transport
        self.lock = asyncio.Lock()

    async def save_authorization(self, tokens, companies):
        matches = [c for c in companies if c.get('tenantName') == 'Demo Company (UK)' and c.get('tenantType') == 'ORGANISATION' and c.get('tenantId')]
        if len(matches) != 1:
            raise ConnectionError('Select exactly the UK Demo Company. Other organisations will not be stored.')
        if not tokens.get('refresh_token'):
            raise ConnectionError('Offline access was not granted. Reconnect and approve offline access.')
        connection = dict(access_token=tokens['access_token'], refresh_token=tokens['refresh_token'], expires_at=time.time() + int(tokens['expires_in']), tenant_id=matches[0]['tenantId'], tenant_name=matches[0]['tenantName'])
        async with self.lock:
            self.store.save(connection)

    async def _refresh(self, data):
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            try:
                r = await client.post(TOKEN_URL, auth=(self.settings.client_id, self.settings.client_secret), data={'grant_type': 'refresh_token', 'refresh_token': data['refresh_token']})
                if r.status_code in (400, 401):
                    raise ConnectionError('Xero could not refresh access. Reconnect the UK Demo Company.')
                r.raise_for_status()
                tokens = r.json()
                updated = dict(data, access_token=tokens['access_token'], refresh_token=tokens['refresh_token'], expires_at=time.time() + int(tokens['expires_in']))
                self.store.save(updated)
                return updated
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                raise ConnectionError('Token refresh failed. Retry later; reconnect if the problem persists.') from None

    async def company(self):
        # Lock includes refresh and rotation, preventing parallel refreshes in one worker.
        async with self.lock:
            data = self.store.load()
            try:
                if data['expires_at'] <= time.time() + 60:
                    data = await self._refresh(data)
                async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                    r = await client.get(CONNECTIONS_URL, headers={'Authorization': 'Bearer ' + data['access_token']})
                    if r.status_code == 401:
                        data = await self._refresh(data)
                        r = await client.get(CONNECTIONS_URL, headers={'Authorization': 'Bearer ' + data['access_token']})
                    r.raise_for_status()
                    connected = r.json()
                    if not any(c.get('tenantId') == data['tenant_id'] for c in connected):
                        raise ConnectionError('Saved tenant is no longer connected. Reconnect after a demo reset or revocation.')
                    return {'status': 'connected', 'tenant_name': data['tenant_name'], 'tenant_id': data['tenant_id'], 'expires_at': data['expires_at']}
            except (httpx.HTTPError, ValueError, TypeError):
                raise ConnectionError('Could not read the saved Xero connection. Retry later or reconnect.') from None

    async def request(self, method, path, *, body=None, params=None, idempotency_key=None):
        """Make a tenant-bound accounting call. No automatic retry of uncertain writes."""
        if not path.startswith('/') or '..' in path:
            raise ConnectionError('Invalid accounting endpoint.')
        async with self.lock:
            data = self.store.load()
            if data['tenant_name'] != 'Demo Company (UK)':
                raise ConnectionError('This learning app only supports the UK Demo Company.')
            if data['expires_at'] <= time.time() + 60:
                data = await self._refresh(data)
            async with httpx.AsyncClient(transport=self.transport, timeout=20, follow_redirects=False) as client:
                for attempt in range(2):
                    headers = {'Authorization': 'Bearer ' + data['access_token'], 'xero-tenant-id': data['tenant_id'], 'Accept': 'application/json'}
                    if idempotency_key:
                        headers['Idempotency-Key'] = idempotency_key
                    try:
                        r = await client.request(method, 'https://api.xero.com/api.xro/2.0' + path, headers=headers, json=body, params=params)
                    except httpx.HTTPError:
                        raise ConnectionError('Xero request interrupted. Creation outcome may be uncertain; inspect Xero before retrying.') from None
                    if r.status_code == 401 and attempt == 0:
                        data = await self._refresh(data)
                        continue
                    if r.status_code == 403:
                        raise ConnectionError('Xero permissions are insufficient. Reconnect and approve contacts and invoice access.')
                    if r.status_code >= 400:
                        raise ConnectionError(f'Xero rejected the request (HTTP {r.status_code}). Check permissions and demo mappings; do not retry uncertain writes blindly.')
                    try:
                        result = r.json()
                    except ValueError:
                        raise ConnectionError('Xero returned an unreadable response. Inspect the destination before retrying writes.') from None
                    if not isinstance(result, dict):
                        raise ConnectionError('Xero returned an unexpected response.')
                    return result
