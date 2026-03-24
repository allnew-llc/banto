# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""CLI subcommands for banto lease — dynamic secrets with TTL."""
from __future__ import annotations

import sys

from .lease import LeaseManager


def cmd_lease_acquire(args: list[str]) -> None:
    """Acquire a new short-lived credential."""
    name = None
    cmd = None
    revoke_cmd = ""
    ttl = 3600

    i = 0
    while i < len(args):
        if args[i] == "--cmd" and i + 1 < len(args):
            cmd = args[i + 1]
            i += 2
        elif args[i] == "--revoke-cmd" and i + 1 < len(args):
            revoke_cmd = args[i + 1]
            i += 2
        elif args[i] == "--ttl" and i + 1 < len(args):
            ttl = int(args[i + 1])
            i += 2
        elif not args[i].startswith("--") and name is None:
            name = args[i]
            i += 1
        else:
            i += 1

    if not name or not cmd:
        print("Usage: banto lease acquire <name> --cmd '<generate>' [--revoke-cmd '<revoke>'] [--ttl 3600]")
        sys.exit(1)

    mgr = LeaseManager()
    try:
        info = mgr.acquire(name=name, ttl_seconds=ttl, cmd=cmd, revoke_cmd=revoke_cmd)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    ttl_display = f"{ttl // 3600}h{(ttl % 3600) // 60}m" if ttl >= 3600 else f"{ttl // 60}m"
    print(f"Lease acquired: {info.lease_id}")
    print(f"  Name:    {name}")
    print(f"  TTL:     {ttl_display}")
    print(f"  Expires: {info.expires_at}")
    if revoke_cmd:
        print(f"  Revoke:  auto on expiry")
    print(f"\n  Value stored in Keychain (retrieve with: banto lease get {info.lease_id})")


def cmd_lease_get(args: list[str]) -> None:
    """Retrieve a lease's credential value (if still active)."""
    if not args:
        print("Usage: banto lease get <lease_id>")
        sys.exit(1)

    mgr = LeaseManager()
    value = mgr.get_value(args[0])
    if value is None:
        print("Lease expired, revoked, or not found.", file=sys.stderr)
        sys.exit(1)
    # Print value to stdout (for piping)
    print(value, end="")


def cmd_lease_revoke(args: list[str]) -> None:
    """Explicitly revoke a lease."""
    if not args:
        print("Usage: banto lease revoke <lease_id>")
        sys.exit(1)

    mgr = LeaseManager()
    if mgr.revoke(args[0]):
        print(f"Revoked: {args[0]}")
    else:
        print(f"Lease not found: {args[0]}", file=sys.stderr)
        sys.exit(1)


def cmd_lease_list(args: list[str]) -> None:
    """List all active leases."""
    mgr = LeaseManager()
    active = mgr.list_leases()

    if not active:
        print("No active leases.")
        return

    print(f"\nActive leases ({len(active)}):\n")
    for lease in active:
        remaining = lease.get("remaining_seconds", 0)
        if remaining >= 3600:
            time_str = f"{remaining // 3600}h {(remaining % 3600) // 60}m"
        elif remaining >= 60:
            time_str = f"{remaining // 60}m"
        else:
            time_str = f"{remaining}s"

        print(f"  {lease['lease_id']}")
        print(f"    Name:      {lease['name']}")
        print(f"    Remaining: {time_str}")
        print(f"    Expires:   {lease['expires_at']}")
        print()


def cmd_lease_cleanup(args: list[str]) -> None:
    """Revoke all expired leases."""
    mgr = LeaseManager()
    count = mgr.cleanup()
    if count:
        print(f"Revoked {count} expired lease(s).")
    else:
        print("No expired leases to clean up.")


LEASE_COMMANDS = {
    "acquire": cmd_lease_acquire,
    "get": cmd_lease_get,
    "revoke": cmd_lease_revoke,
    "list": cmd_lease_list,
    "cleanup": cmd_lease_cleanup,
}


def cmd_lease_dispatch(args: list[str]) -> None:
    """Dispatch banto lease <subcommand>."""
    if not args or args[0] in ("-h", "--help"):
        print("banto lease: Dynamic secrets with TTL\n")
        print("Usage: banto lease <command> [args]\n")
        print("Commands:")
        print("  acquire <name> --cmd '<gen>' [--revoke-cmd '<rev>'] [--ttl N]")
        print("                      Generate a short-lived credential")
        print("  get <lease_id>      Retrieve credential (stdout, for piping)")
        print("  revoke <lease_id>   Explicitly revoke a lease")
        print("  list                Show all active leases")
        print("  cleanup             Revoke all expired leases")
        sys.exit(0)

    sub = args[0]
    if sub not in LEASE_COMMANDS:
        print(f"Unknown lease command: {sub}", file=sys.stderr)
        print(f"Available: {', '.join(LEASE_COMMANDS)}", file=sys.stderr)
        sys.exit(1)

    LEASE_COMMANDS[sub](args[1:])
