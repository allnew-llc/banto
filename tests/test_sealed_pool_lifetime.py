"""Fake-framework proof that the non-extractability query passes and drains one pool.

This does not create a Secure Enclave key and does not import the signer.
A passing result here is not a physical non-extractability result.
"""
import ast
import ctypes
from pathlib import Path

import pytest


PUBLIC = "04" + "ab" * 32


def _load():
    path = Path(__file__).with_name("test_sealed_signer.py")
    tree = ast.parse(path.read_text())
    func = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "test_enclave_roundtrip_and_non_extractable")
    scope = {"ctypes": ctypes, "pytest": pytest, "_signed_native_case": lambda _operation: None}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(path), "exec"), scope)
    return func, scope["test_enclave_roundtrip_and_non_extractable"]


def _signer(copy, *, cfdict_error=False):
    released = []
    created = []

    class Signer:
        class SealedSignerError(Exception):
            pass

        class _SEC:
            @staticmethod
            def SecItemCopyMatching(query, out_ref):
                return copy(query, out_ref)

        class _CF:
            kCFBooleanTrue = "true"

            @staticmethod
            def CFRelease(ref):
                released.append(("cf", ref))

        @staticmethod
        def enclave_usable():
            return True

        @staticmethod
        def delete_signing_key(key_id):
            released.append(("delete", key_id))

        @staticmethod
        def create_signing_key(key_id, require_user_presence=False):
            return {"sealed": True, "curve": "P-256", "public_key_x963_hex": PUBLIC}

        @staticmethod
        def export_public_key(key_id):
            return {"public_key_x963_hex": PUBLIC}

        @staticmethod
        def sign(key_id, payload):
            return b"\x30"

        @staticmethod
        def _const(lib, name):
            return name

        @staticmethod
        def _tag(key_id):
            return b"tag-" + key_id.encode()

        @staticmethod
        def _cfdata(data, pool):
            ref = ("data", bytes(data))
            pool.append(ref)
            created.append(ref)
            return ref

        @staticmethod
        def _cfdict(pairs, pool):
            if cfdict_error:
                raise RuntimeError("dict failed after data was pooled")
            ref = ("dict", tuple(pairs))
            pool.append(ref)
            created.append(ref)
            return ref

        @staticmethod
        def _drain(pool):
            while pool:
                ref = pool.pop()
                released.append(("drain", ref))

    return Signer(), created, released


def _call(copy, **kwargs):
    _func, test = _load()
    signer, created, released = _signer(copy, **kwargs)
    scope = test.__globals__
    scope["sealed_signer"] = signer
    scope["ecdsa_p256"] = type("Ecdsa", (), {"verify": staticmethod(lambda *args: True)})
    return test, created, released


def test_source_passes_one_pool_and_drains_it_in_finally():
    func, _test = _load()

    def attr(call, name):
        return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == name)

    calls = [node for node in ast.walk(func) if isinstance(node, ast.Call)]
    cfdata = [call for call in calls if attr(call, "_cfdata")]
    cfdict = [call for call in calls if attr(call, "_cfdict")]
    assert len(cfdata) == 1 and len(cfdata[0].args) == 2 and cfdata[0].args[1].id == "pool"
    assert len(cfdict) == 1 and len(cfdict[0].args) == 2 and cfdict[0].args[1].id == "pool"
    found = False
    for node in ast.walk(func):
        if isinstance(node, ast.Try) and any(attr(call, "_drain") for stmt in node.finalbody for call in ast.walk(stmt)):
            found = True
    assert found
    releases = [call for call in calls if attr(call, "CFRelease")]
    assert len(releases) == 1 and releases[0].args[0].id == "out"


def test_missing_key_releases_pooled_objects_once():
    def copy(query, out_ref):
        out_ref._obj.value = None
        return -25300

    test, created, released = _call(copy)
    test()
    drained = [item for kind, item in released if kind == "drain"]
    assert drained == list(reversed(created))
    assert created and len(drained) == len(created) == 2
    assert not any(kind == "cf" for kind, _item in released)
    assert [item for kind, item in released if kind == "delete"] == ["banto-tcb-selftest", "banto-tcb-selftest"]


def test_extractable_result_is_released_without_reading_and_still_fails():
    sentinel = 0x10

    def copy(query, out_ref):
        out_ref._obj.value = sentinel
        return 0

    test, created, released = _call(copy)
    with pytest.raises(AssertionError):
        test()
    released_values = [item.value for kind, item in released if kind == "cf"]
    assert released_values == [sentinel]
    assert [item for kind, item in released if kind == "drain"] == list(reversed(created))


def test_query_error_still_drains_the_pool_once():
    def copy(query, out_ref):
        raise OSError("native query failed")

    test, created, released = _call(copy)
    with pytest.raises(OSError):
        test()
    assert [item for kind, item in released if kind == "drain"] == list(reversed(created))
    assert not any(kind == "cf" for kind, _item in released)


def test_dict_failure_after_data_create_drains_that_data_once():
    def copy(query, out_ref):
        raise AssertionError("query must not run")

    test, created, released = _call(copy, cfdict_error=True)
    with pytest.raises(RuntimeError):
        test()
    assert [item[0] for item in created] == ["data"]
    assert [item for kind, item in released if kind == "drain"] == list(reversed(created))
