"""Route supported legacy CLI commands through the installed broker."""
import json

from .broker_client import BrokerClient, BrokerError, runtime_dir


def route_if_enabled(args: list[str]) -> bool:
    if not (runtime_dir() / "required").exists():
        return False
    operation = None
    arguments = {}
    if args[0] == "register" and len(args) <= 2:
        operation = "banto_register_key"
        arguments = {"provider": args[1] if len(args) == 2 else ""}
    elif args in (["register-asc"], ["asc-register"]):
        operation = "register_asc"
    elif args == ["register-x-oauth"]:
        operation = "register_x_oauth"
    elif args[0] == "sync":
        if args[1:] == ["status"]:
            operation = "banto_sync_status"
        elif args[1:] == ["audit"]:
            operation = "banto_sync_audit"
        elif args[1:] == ["validate"]:
            operation = "banto_validate"
        elif args[1:] == ["validate", "--keychain"]:
            operation = "banto_validate_keychain"
        elif args[1:] == ["push"]:
            operation = "banto_sync_push"
    if operation:
        try:
            print(json.dumps(BrokerClient().call(operation, **arguments), ensure_ascii=False))
        except BrokerError:
            raise SystemExit("Broker operation failed; no direct Keychain fallback. Check service status.") from None
        return True
    # Existing metadata and Secure Enclave commands keep their behavior. Other
    # legacy commands need explicit migration, rather than a silent bypass.
    if args[0] in {"store", "delete", "sync", "lease"}:
        raise SystemExit("This legacy command is disabled in broker mode. Use banto register or an allowlisted broker operation.")
    return False
