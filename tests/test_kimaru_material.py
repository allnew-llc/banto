"""No actual Keychain, credentials, Azure, network or paid calls."""
import base64
import copy
import json
import unittest
from unittest.mock import patch
from banto import broker
from banto import kimaru_material as material

ARGS = dict(environment='staging', purpose='db-key-rewrap-url', candidate_sha='a' * 40, approved_region='japaneast', owner_confirm=True)

class Store:
    def __init__(self): self.values = {}; self.calls = []
    def get(self, name): self.calls.append(('get', name)); return self.values.get(name)
    def exists(self, name): self.calls.append(('exists', name)); return name in self.values
    def store(self, name, value): self.calls.append(('store', name)); self.values[name] = value; return True

class Azure:
    def __init__(self, args=ARGS):
        self.c = material.context(**args); self.calls = []; self.versions = []; self.key = None; self.deleted = False; self.unknown = False
        self.vault = {'id': self.c['vault_id'], 'location': 'japaneast', 'tags': {'service': 'fm-sme', 'environment': 'staging'},
            'properties': {'enableRbacAuthorization': True, 'enablePurgeProtection': True, 'enableSoftDelete': True}}
    def request(self, method, path, payload=None, arm=False):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if arm: return self.vault
        if path.startswith('/deleted'): return {'recovery': 'synthetic'} if self.deleted else None
        if method == 'PUT':
            self.versions.append({'id': self.c['url'] + '/secrets/' + self.c['name'] + '/' + '1' * 32, 'attributes': {'enabled': True}, 'tags': payload['tags']})
            if self.unknown: raise TimeoutError('synthetic secret body must never leave service')
            return {'value': payload['value']}
        if '/versions?' in path: return {'value': copy.deepcopy(self.versions)}
        if method == 'POST':
            self.key = {'key': {'kid': self.c['url'] + '/keys/' + self.c['name'] + '/' + '1' * 32,
                'kty': 'RSA', 'key_ops': ['wrapKey', 'unwrapKey'], 'n': base64.urlsafe_b64encode(b'\x80' + b'\0' * 383).decode().rstrip('=')},
                'attributes': {'enabled': True}, 'tags': payload['tags']}
            return copy.deepcopy(self.key)
        if path.startswith('/keys/'): return copy.deepcopy(self.key)
        raise AssertionError('Unexpected request')

class KimaruMaterial(unittest.TestCase):
    def setUp(self):
        self.service = patch.object(broker, 'IN_SERVICE', True); self.service.start(); self.addCleanup(self.service.stop)
    def call(self, azure, store, args=ARGS):
        return material.provision(**args, _transport=azure, _store=store)

    def test_dispatch_contract_has_no_stored_secret_inventory(self):
        data = broker.dispatch('kimaru_material_capabilities', {})
        self.assertEqual(data['contract'], 'kimaru-material-v1'); self.assertFalse(data['secret_values_returned'])
        with self.assertRaises(broker.BrokerError): broker.dispatch('kimaru_material_provision', {**ARGS, 'url': 'https://attacker.invalid'})
        with patch.object(broker, 'IN_SERVICE', False):
            with self.assertRaises(Exception): self.call(Azure(), Store())

    def test_invalid_arguments_fail_before_transport_or_store(self):
        for patch_args in ({'environment': 'other'}, {'purpose': 'arbitrary'}, {'candidate_sha': 'bad'}, {'approved_region': 'eastus'}, {'owner_confirm': 1}):
            azure, store = Azure(), Store()
            with self.assertRaises(material.MaterialError): self.call(azure, store, {**ARGS, **patch_args})
            self.assertEqual(azure.calls, []); self.assertEqual(store.calls, [])

    def test_wrong_vault_region_tags_or_protection_does_not_read_or_mint_local_material(self):
        for field, value in [('location', 'eastus'), ('tags', {'service': 'other'}), ('id', '/other'), ('properties', {'enableRbacAuthorization': False})]:
            azure, store = Azure(), Store(); azure.vault[field] = value
            with self.assertRaises(material.MaterialError): self.call(azure, store)
            self.assertEqual(store.calls, []); self.assertFalse(any(x[0] in ('PUT', 'POST') for x in azure.calls))

    def test_key_only_url_issuance_and_replay_return_no_values_or_extra_versions(self):
        azure, store = Azure(), Store()
        first = self.call(azure, store); second = self.call(azure, store)
        self.assertEqual(first['state'], 'created'); self.assertEqual(second['state'], 'already_verified')
        self.assertEqual(sum(x[0] == 'PUT' for x in azure.calls), 1)
        self.assertEqual(len(azure.versions), 1)
        self.assertTrue(all(store_value not in json.dumps(first) for store_value in store.values.values()))
        self.assertFalse(any('/secrets/' in path and '/versions?' not in path and method == 'GET' for method, path, _ in azure.calls))

    def test_remote_conflict_or_soft_delete_never_rotates(self):
        azure, store = Azure(), Store(); self.call(azure, store)
        for change in ('fingerprint', 'disabled', 'multiple', 'missing-local'):
            changed, local = copy.deepcopy(azure), copy.deepcopy(store); changed.calls = []
            if change == 'fingerprint': changed.versions[0]['tags']['material-sha256'] = 'b' * 64
            elif change == 'disabled': changed.versions[0]['attributes']['enabled'] = False
            elif change == 'multiple': changed.versions.append(copy.deepcopy(changed.versions[0]))
            else: local.values.clear()
            with self.assertRaises(material.MaterialError): self.call(changed, local)
            self.assertFalse(any(x[0] in ('PUT', 'POST') for x in changed.calls))
        azure = Azure(); azure.deleted = True; local = Store()
        with self.assertRaises(material.MaterialError): self.call(azure, local)
        self.assertEqual(local.calls, [])

    def test_unknown_write_result_is_not_retried_and_explicit_metadata_readback_can_resume(self):
        azure, store = Azure(), Store(); azure.unknown = True
        with self.assertRaisesRegex(material.MaterialError, '^KIMARU_MATERIAL_FAILED_OR_UNKNOWN$'): self.call(azure, store)
        self.assertEqual(sum(x[0] == 'PUT' for x in azure.calls), 1)
        azure.unknown = False
        self.assertEqual(self.call(azure, store)['state'], 'already_verified')
        self.assertEqual(sum(x[0] == 'PUT' for x in azure.calls), 1)

    def test_all_generated_kinds_and_derived_dependencies_are_fixed(self):
        store = Store()
        for purpose in material.GENERATED:
            args = {**ARGS, 'purpose': purpose}; azure = Azure(args)
            result = self.call(azure, store, args)
            self.assertEqual(result['purpose'], purpose)
            self.assertNotIn(store.values[f'kimaru-staging-{purpose}'], json.dumps(result))
        bad = Store(); bad.values['kimaru-staging-db-key-rewrap-url'] = 'postgresql://other:bad@attacker.invalid/db'
        with self.assertRaisesRegex(material.MaterialError, 'KIMARU_MATERIAL_INVALID'): self.call(Azure(), bad)
        args = {**ARGS, 'purpose': 'db-owner-url'}
        with self.assertRaisesRegex(material.MaterialError, 'KIMARU_MATERIAL_DEPENDENCY_REQUIRED'): self.call(Azure(args), Store(), args)

    def test_external_keys_require_personal_popup_and_correct_stripe_mode(self):
        for purpose in material.EXTERNAL:
            args = {**ARGS, 'purpose': purpose}; azure, store = Azure(args), Store()
            with self.assertRaisesRegex(material.MaterialError, 'KIMARU_REGISTER_KEY_REQUIRED'): self.call(azure, store, args)
            self.assertFalse(any(x[0] == 'PUT' for x in azure.calls))
        args = {**ARGS, 'purpose': 'stripe-secret'}; store = Store(); store.values['kimaru-staging-stripe-secret'] = 'sk_live_' + 'SYNTHETIC' * 8
        with self.assertRaisesRegex(material.MaterialError, 'KIMARU_MATERIAL_INVALID'): self.call(Azure(args), store, args)

    def test_kek_is_created_in_azure_without_extracting_or_storing_a_private_key(self):
        args = {**ARGS, 'purpose': 'data-kek'}; azure, store = Azure(args), Store()
        first = self.call(azure, store, args); second = self.call(azure, store, args)
        self.assertEqual(first['state'], 'created'); self.assertEqual(second['state'], 'already_verified')
        self.assertEqual(store.calls, []); self.assertEqual(sum(x[0] == 'POST' for x in azure.calls), 1)
        self.assertEqual(set(first), {'contract', 'environment', 'purpose', 'candidate', 'state', 'uri'})
        for patch_key in ({'d': 'SYNTHETIC-PRIVATE'}, {'kty': 'EC'}, {'n': 'AA'}, {'key_ops': ['sign']}):
            changed = copy.deepcopy(azure); changed.key['key'].update(patch_key); changed.calls = []
            with self.assertRaises(material.MaterialError): self.call(changed, Store(), args)
            self.assertFalse(any(x[0] == 'POST' for x in changed.calls))

    def test_local_store_failure_does_not_push_to_azure(self):
        azure, store = Azure(), Store()
        with patch.object(store, 'store', return_value=False):
            with self.assertRaisesRegex(material.MaterialError, 'KIMARU_KEYCHAIN_STORE_FAILED'): self.call(azure, store)
        self.assertFalse(any(x[0] == 'PUT' for x in azure.calls))

class KimaruMaterialTransport(unittest.TestCase):
    def test_azure_tokens_stay_in_service_and_body_is_not_an_argument_or_env(self):
        c = material.context(**ARGS)
        token = 'SYNTHETIC-BEARER-DO-NOT-RETURN'
        from types import SimpleNamespace
        from unittest.mock import Mock
        transport = material.AzureTransport(c)
        response = Mock(); response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"value":[]}'
        transport.opener = Mock(); transport.opener.open.return_value = response
        with patch('banto.kimaru_material.shutil.which', return_value='/synthetic/az'), patch('banto.kimaru_material.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=token)) as run:
            self.assertEqual(transport.request('GET', '/secrets/fm-sme-db-key-rewrap-url/versions?api-version=2025-07-01'), {'value': []})
            kwargs = run.call_args.kwargs
            self.assertNotIn(token, json.dumps(run.call_args.args)); self.assertNotIn(token, json.dumps(kwargs['env']))
            self.assertEqual(kwargs['env'].keys() - {'PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR','AZURE_CONFIG_DIR'}, set())
            request = transport.opener.open.call_args.args[0]
            self.assertEqual(request.full_url.split('/secrets/')[0], c['url'])
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + token)

    def test_transport_rejects_foreign_path_before_login(self):
        transport = material.AzureTransport(material.context(**ARGS))
        for path in ('https://attacker.invalid', '//attacker.invalid/..', '/keys/../../other'):
            with patch('banto.kimaru_material.subprocess.run') as run:
                with self.assertRaises(material.MaterialError): transport.request('GET', path)
                run.assert_not_called()

    def test_rpc_uses_actual_common_dispatch_and_returns_only_metadata(self):
        import tempfile, threading
        from pathlib import Path
        from banto.broker_client import BrokerClient
        azure, store = Azure(), Store(); original = material.provision
        with tempfile.TemporaryDirectory() as directory, patch.object(broker, 'IN_SERVICE', True), patch.object(material, 'provision', side_effect=lambda **kw: original(**kw, _transport=azure, _store=store)):
            root = Path(directory); root.chmod(0o700); path = root/'broker.sock'
            with broker.Server(str(path), broker.Handler) as server:
                path.chmod(0o600); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    result = BrokerClient(path, timeout=5).call('kimaru_material_provision', **ARGS)
                    self.assertEqual(result['state'], 'created')
                    for value in store.values.values(): self.assertNotIn(value, json.dumps(result))
                    with self.assertRaises(Exception): BrokerClient(path, timeout=5).call('kimaru_material_provision', **ARGS, value='SYNTHETIC-SECRET-INPUT')
                finally: server.shutdown(); thread.join(5)

if __name__ == '__main__': unittest.main()

