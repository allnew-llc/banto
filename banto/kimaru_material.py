"""Fixed Kimaru material operations, executed only inside the common broker.

No secret-return operation, caller URL/secret/command, automatic rotation, direct
fallback or provider key issuance. Azure keeps KEK private material; Keychain
holds generated credentials. Runtime DEKs remain in the private Azure workload.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request

SUBSCRIPTION = '49805a83-6ad1-489f-886d-891dbf12a2fc'
VERSION = '2025-07-01'
GENERATED = {
    'bootstrap-pg-password': 'admin', 'bootstrap-runtime-password': 'hex',
    'auth-secret': 'hex', 'generation-quota-secret': 'hex',
    'db-backup-url': 'backup', 'db-key-rewrap-url': 'key_rewrap',
    'restore-password': 'admin', 'db-owner-url': 'owner',
    'db-runtime-url': 'runtime', 'restore-url': 'restore',
}
EXTERNAL = ('stripe-secret', 'stripe-webhook-secret', 'gemini-api-key')
PURPOSES = (*GENERATED, *EXTERNAL, 'data-kek')

class MaterialError(RuntimeError):
    pass


def capabilities() -> dict:
    return {'contract': 'kimaru-material-v1', 'environments': ['staging', 'production'],
            'purposes': list(PURPOSES), 'secret_values_returned': False,
            'automatic_rotation': False}


def context(environment: str, purpose: str, candidate_sha: str, approved_region: str, owner_confirm: bool) -> dict:
    if (environment not in ('staging', 'production') or purpose not in PURPOSES
            or not isinstance(candidate_sha, str) or not re.fullmatch('[a-f0-9]{40}', candidate_sha)
            or approved_region != 'japaneast' or owner_confirm is not True):
        raise MaterialError('KIMARU_MATERIAL_ARGUMENTS_INVALID')
    suffix = 'stg' if environment == 'staging' else 'prd'
    vault = f'fm-sme-kv-{suffix}-49805a'
    return {'environment': environment, 'purpose': purpose, 'sha': candidate_sha,
            'vault': vault, 'url': f'https://{vault}.vault.azure.net', 'suffix': suffix,
            'vault_id': f'/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-fm-sme-{environment}/providers/Microsoft.KeyVault/vaults/{vault}',
            'name': 'fm-sme-data-kek' if purpose == 'data-kek' else f'fm-sme-{purpose}',
            'account': f'kimaru-{environment}-{purpose}'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AzureTransport:
    """Tokens/payloads stay inside banto; no proxy/redirect/retry/output body."""
    def __init__(self, c: dict):
        self.c = c
        self.tokens: dict[str, str] = {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method: str, path: str, payload: dict | None = None, *, arm: bool = False):
        from .broker import observe_secret
        audience = 'https://management.azure.com/' if arm else 'https://vault.azure.net'
        base = 'https://management.azure.com' if arm else self.c['url']
        if not path.startswith('/') or '://' in path or '..' in path:
            raise MaterialError('KIMARU_MATERIAL_PATH_INVALID')
        if audience not in self.tokens:
            az = shutil.which('az')
            if not az:
                raise MaterialError('KIMARU_AZURE_LOGIN_REQUIRED')
            # No app secret is added to argv or environment. The owner logs in to az.
            safe_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'AZURE_CONFIG_DIR')}
            result = subprocess.run([az, 'account', 'get-access-token', '--subscription', SUBSCRIPTION,
                '--resource', audience, '--query', 'accessToken', '-o', 'tsv'],
                capture_output=True, text=True, timeout=30, env=safe_env)
            token = result.stdout.strip()
            if result.returncode or not token or len(token) > 16384:
                raise MaterialError('KIMARU_AZURE_LOGIN_REQUIRED')
            observe_secret(token)
            self.tokens[audience] = token
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(base + path, data=body, method=method,
            headers={'Authorization': 'Bearer ' + self.tokens[audience], 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(262145)
                if len(data) > 262144:
                    raise MaterialError('KIMARU_MATERIAL_RESPONSE_INVALID')
                return json.loads(data)
        except urllib.error.HTTPError as error:
            error.close()
            if method == 'GET' and error.code == 404:
                return None
            raise MaterialError('KIMARU_MATERIAL_REMOTE_FAILED_OR_UNKNOWN') from None
        except (OSError, ValueError):
            raise MaterialError('KIMARU_MATERIAL_REMOTE_FAILED_OR_UNKNOWN') from None


def _vault(c: dict, transport) -> None:
    data = transport.request('GET', c['vault_id'] + '?api-version=2023-07-01', arm=True)
    if not isinstance(data, dict):
        raise MaterialError('KIMARU_VAULT_INVALID')
    props, tags = data.get('properties', {}), data.get('tags', {})
    if (data.get('id', '').lower() != c['vault_id'].lower() or data.get('location') != 'japaneast'
            or tags.get('service') != 'fm-sme' or tags.get('environment') != c['environment']
            or any(props.get(k) is not True for k in ('enableRbacAuthorization', 'enablePurgeProtection', 'enableSoftDelete'))):
        raise MaterialError('KIMARU_VAULT_INVALID')


def _tags(c: dict, fingerprint: str | None = None) -> dict:
    result = {'service': 'fm-sme', 'environment': c['environment'], 'purpose': c['purpose'],
              'issuer': 'banto', 'material-contract': 'kimaru-material-v1', 'candidate': c['sha']}
    if fingerprint:
        result['material-sha256'] = fingerprint
    return result


def _material(c: dict, store) -> str:
    from .broker import observe_secret
    value = store.get(c['account'])  # Service-only, exactly one approved account.
    if value is None:
        kind = GENERATED.get(c['purpose'])
        if kind is None:
            raise MaterialError('KIMARU_REGISTER_KEY_REQUIRED')
        if kind in ('owner', 'runtime', 'restore'):
            dependency = {'owner': 'bootstrap-pg-password', 'runtime': 'bootstrap-runtime-password', 'restore': 'restore-password'}[kind]
            password = store.get(f"kimaru-{c['environment']}-{dependency}")
            if not password:
                raise MaterialError('KIMARU_MATERIAL_DEPENDENCY_REQUIRED')
            value = password
        elif kind == 'admin':
            value = secrets.token_hex(30) + 'Az9!'
        else:
            value = secrets.token_hex(32)
        if kind in ('owner', 'runtime', 'restore', 'backup', 'key_rewrap'):
            role = {'owner': 'owner', 'runtime': 'runtime', 'restore': 'restore_owner', 'backup': 'backup', 'key_rewrap': 'key_rewrap'}[kind]
            server = 'allnew-fm-pg-' + ('restore-' if kind == 'restore' else '') + c['suffix']
            db = 'fm_sme_' + ('restore_' if kind == 'restore' else '') + c['environment']
            from urllib.parse import quote
            tls = 'sslmode=verify-full&sslrootcert=system' if kind == 'restore' else 'sslmode=verify-full' if kind == 'owner' else 'sslmode=require&sslaccept=strict'
            value = f'postgresql://fm_sme_{role}:{quote(value, safe="")}@{server}.postgres.database.azure.com:5432/{db}?{tls}'
        observe_secret(value)
        if not store.store(c['account'], value):
            raise MaterialError('KIMARU_KEYCHAIN_STORE_FAILED')
    observe_secret(value)
    if not value or not isinstance(value, str):
        raise MaterialError('KIMARU_MATERIAL_INVALID')
    kind = GENERATED.get(c['purpose'])
    if kind == 'admin' and not re.fullmatch('[a-f0-9]{60}Az9!', value):
        raise MaterialError('KIMARU_MATERIAL_INVALID')
    if kind == 'hex' and not re.fullmatch('[a-f0-9]{64}', value):
        raise MaterialError('KIMARU_MATERIAL_INVALID')
    if kind in ('owner', 'runtime', 'restore', 'backup', 'key_rewrap'):
        from urllib.parse import urlsplit, unquote
        try:
            parsed = urlsplit(value)
            role = {'owner': 'owner', 'runtime': 'runtime', 'restore': 'restore_owner', 'backup': 'backup', 'key_rewrap': 'key_rewrap'}[kind]
            restore = 'restore-' if kind == 'restore' else ''
            db = 'fm_sme_' + ('restore_' if kind == 'restore' else '') + c['environment']
            tls = 'sslmode=verify-full&sslrootcert=system' if kind == 'restore' else 'sslmode=verify-full' if kind == 'owner' else 'sslmode=require&sslaccept=strict'
            password_pattern = '[a-f0-9]{60}Az9!' if kind in ('owner', 'restore') else '[a-f0-9]{64}'
            valid = (parsed.scheme == 'postgresql' and parsed.hostname == f"allnew-fm-pg-{restore}{c['suffix']}.postgres.database.azure.com"
                and parsed.port == 5432 and parsed.username == f'fm_sme_{role}' and parsed.path == '/' + db
                and not parsed.fragment and parsed.query == tls
                and re.fullmatch(password_pattern, unquote(parsed.password or '')))
        except ValueError:
            valid = False
        if not valid:
            raise MaterialError('KIMARU_MATERIAL_INVALID')
    if c['purpose'] in ('stripe-secret', 'stripe-webhook-secret'):
        prefix = 'whsec_' if c['purpose'] == 'stripe-webhook-secret' else ('sk_test_' if c['environment'] == 'staging' else 'sk_live_')
        if not value.startswith(prefix) or len(value) < 24 or any(x.isspace() for x in value):
            raise MaterialError('KIMARU_MATERIAL_INVALID')
    if c['purpose'] == 'gemini-api-key' and (not value.startswith('AIza') or len(value) < 30 or any(x.isspace() for x in value)):
        raise MaterialError('KIMARU_MATERIAL_INVALID')
    return value


def _versions(c: dict, transport):
    data = transport.request('GET', f"/secrets/{c['name']}/versions?api-version={VERSION}")
    if data is None:
        return []
    if not isinstance(data, dict) or data.get('nextLink') or not isinstance(data.get('value'), list):
        raise MaterialError('KIMARU_MATERIAL_METADATA_CONFLICT')
    return data['value']


def _secret(c: dict, transport, store) -> dict:
    versions = _versions(c, transport)
    # An existing cloud value must never cause an automatic local credential mint.
    if versions and not store.exists(c['account']):
        raise MaterialError('KIMARU_MATERIAL_REMOTE_CONFLICT')
    if not versions and transport.request('GET', f"/deletedsecrets/{c['name']}?api-version={VERSION}") is not None:
        raise MaterialError('KIMARU_MATERIAL_DELETED_CONFLICT')
    value = _material(c, store)
    fingerprint = hashlib.sha256(value.encode()).hexdigest()
    tags = _tags(c, fingerprint)
    created = False
    if not versions:
        # A timeout here is unknown. Never repeat PUT automatically.
        transport.request('PUT', f"/secrets/{c['name']}?api-version={VERSION}",
            {'value': value, 'attributes': {'enabled': True}, 'tags': tags})
        created = True
        versions = _versions(c, transport)
    if len(versions) != 1:
        raise MaterialError('KIMARU_MATERIAL_REMOTE_CONFLICT')
    row = versions[0]
    expected = {k: v for k, v in tags.items() if k != 'candidate'}
    if (row.get('attributes', {}).get('enabled') is not True
            or any(row.get('tags', {}).get(k) != v for k, v in expected.items())
            or not re.fullmatch(re.escape(c['url'] + '/secrets/' + c['name'] + '/') + '[a-f0-9]{32}', row.get('id', ''))):
        raise MaterialError('KIMARU_MATERIAL_REMOTE_CONFLICT')
    return {'contract': 'kimaru-material-v1', 'environment': c['environment'], 'purpose': c['purpose'],
            'candidate': c['sha'], 'state': 'created' if created else 'already_verified',
            'uri': row['id'], 'fingerprint': fingerprint}


def _key(c: dict, transport) -> dict:
    path = f"/keys/{c['name']}?api-version={VERSION}"
    row = transport.request('GET', path)
    created = False
    if row is None:
        if transport.request('GET', f"/deletedkeys/{c['name']}?api-version={VERSION}") is not None:
            raise MaterialError('KIMARU_MATERIAL_DELETED_CONFLICT')
        transport.request('POST', f"/keys/{c['name']}/create?api-version={VERSION}",
            {'kty': 'RSA', 'key_size': 3072, 'key_ops': ['wrapKey', 'unwrapKey'],
             'attributes': {'enabled': True}, 'tags': _tags(c)})
        created = True
        row = transport.request('GET', path)
    if not isinstance(row, dict):
        raise MaterialError('KIMARU_MATERIAL_REMOTE_CONFLICT')
    key = row.get('key', {})
    import base64
    try:
        n = key['n']; modulus = base64.urlsafe_b64decode(n + '=' * (-len(n) % 4))
    except (KeyError, TypeError, ValueError):
        raise MaterialError('KIMARU_MATERIAL_REMOTE_CONFLICT') from None
    if (key.get('kty') != 'RSA' or sorted(key.get('key_ops', [])) != ['unwrapKey', 'wrapKey']
            or len(modulus) != 384 or int.from_bytes(modulus, 'big').bit_length() != 3072
            or any(key.get(k) is not None for k in ('d', 'p', 'q', 'dp', 'dq', 'qi', 'k'))
            or row.get('attributes', {}).get('enabled') is not True
            or any(row.get('tags', {}).get(k) != v for k, v in _tags(c).items() if k != 'candidate')
            or not re.fullmatch(re.escape(c['url'] + '/keys/' + c['name'] + '/') + '[a-f0-9]{32}', key.get('kid', ''))):
        raise MaterialError('KIMARU_MATERIAL_REMOTE_CONFLICT')
    return {'contract': 'kimaru-material-v1', 'environment': c['environment'], 'purpose': c['purpose'],
            'candidate': c['sha'], 'state': 'created' if created else 'already_verified', 'uri': key['kid']}


def provision(environment: str, purpose: str, candidate_sha: str, approved_region: str, owner_confirm: bool, *, _transport=None, _store=None) -> dict:
    from .broker import require_service, IN_SERVICE
    require_service()
    # This operation cannot be run as an imported direct-storage library even
    # when an older machine hasn't yet enabled the common process guard.
    if not IN_SERVICE:
        raise MaterialError('KIMARU_COMMON_BROKER_REQUIRED')
    c = context(environment, purpose, candidate_sha, approved_region, owner_confirm)
    transport = _transport or AzureTransport(c)
    try:
        _vault(c, transport)
        if purpose == 'data-kek':
            return _key(c, transport)
        if _store is None:
            from .keychain import KeychainStore
            from .sync.config import SyncConfig
            _store = KeychainStore(service_prefix=SyncConfig.load().keychain_service)
        return _secret(c, transport, _store)
    except MaterialError:
        raise
    except Exception:
        raise MaterialError('KIMARU_MATERIAL_FAILED_OR_UNKNOWN') from None
