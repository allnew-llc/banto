"""Tests for stale hold timeout (automatic voiding of expired holds)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from banto.guard import CostGuard


@pytest.fixture()
def tmp_env(tmp_path: Path):
    """Create a minimal config + pricing + data dir for CostGuard."""
    config = {
        "monthly_limit_usd": 100.0,
        "hold_timeout_hours": 24,
        "provider_limits": {},
        "model_limits": {},
        "providers": {},
    }
    pricing = {
        "test-model": {
            "type": "per_token",
            "input_per_1k": 0.001,
            "output_per_1k": 0.002,
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_text(json.dumps(pricing), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return config_path, data_dir


def _make_guard(config_path: Path, data_dir: Path) -> CostGuard:
    return CostGuard(
        config_path=str(config_path),
        caller="test",
        data_dir=str(data_dir),
    )


def _inject_hold_entry(
    data_dir: Path,
    hold_id: str,
    cost_usd: float,
    hours_ago: float,
):
    """Write a usage file with a hold entry created hours_ago hours in the past."""
    now = datetime.now(timezone.utc)
    held_at = now - timedelta(hours=hours_ago)
    month_str = f"{now.year}-{now.month:02d}"
    usage_path = data_dir / f"usage_{now.year}_{now.month:02d}.json"

    # Load existing or create new
    if usage_path.exists():
        data = json.loads(usage_path.read_text(encoding="utf-8"))
    else:
        data = {
            "month": month_str,
            "total_usd": 0.0,
            "entry_count": 0,
            "entries": [],
        }

    entry = {
        "timestamp": held_at.isoformat(timespec="seconds"),
        "model": "test-model",
        "provider": "test",
        "operation": "hold",
        "status": "hold",
        "hold_id": hold_id,
        "params": {"n": 1, "quality": "standard", "size": "1024x1024",
                    "input_tokens": 0, "output_tokens": 1000},
        "cost_usd": cost_usd,
        "cumulative_usd": data["total_usd"] + cost_usd,
        "caller": "test",
    }
    data["entries"].append(entry)
    data["total_usd"] = sum(e.get("cost_usd", 0) for e in data["entries"])
    data["entry_count"] = len(data["entries"])

    usage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return usage_path


class TestHoldTimeoutVoiding:
    """Tests for _void_stale_holds and its integration with _update_usage."""

    def test_old_hold_gets_voided(self, tmp_env):
        """A hold older than 24h should be voided on next _update_usage."""
        config_path, data_dir = tmp_env
        _inject_hold_entry(data_dir, "h_old001", 0.10, hours_ago=25)

        guard = _make_guard(config_path, data_dir)
        # Trigger _update_usage by recording new usage
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        # Verify the hold was voided
        usage_path = guard._get_usage_file_path()
        data = json.loads(usage_path.read_text(encoding="utf-8"))

        hold_entries = [e for e in data["entries"] if e.get("hold_id") == "h_old001"]
        assert len(hold_entries) == 1
        assert hold_entries[0]["status"] == "voided_timeout"
        assert hold_entries[0]["cost_usd"] == 0
        assert hold_entries[0]["hold_cost_usd"] == 0.10
        assert "voided_at" in hold_entries[0]

    def test_young_hold_remains(self, tmp_env):
        """A hold younger than 24h should NOT be voided."""
        config_path, data_dir = tmp_env
        _inject_hold_entry(data_dir, "h_young001", 0.10, hours_ago=12)

        guard = _make_guard(config_path, data_dir)
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        usage_path = guard._get_usage_file_path()
        data = json.loads(usage_path.read_text(encoding="utf-8"))

        hold_entries = [e for e in data["entries"] if e.get("hold_id") == "h_young001"]
        assert len(hold_entries) == 1
        assert hold_entries[0]["status"] == "hold"
        assert hold_entries[0]["cost_usd"] == 0.10

    def test_voided_hold_releases_budget(self, tmp_env):
        """After a stale hold is voided, its cost no longer counts toward total."""
        config_path, data_dir = tmp_env
        _inject_hold_entry(data_dir, "h_budget001", 10.0, hours_ago=25)

        guard = _make_guard(config_path, data_dir)

        # Before voiding, the hold costs 10.0
        # After voiding, the hold should not count
        # Record a tiny usage to trigger _update_usage
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        usage_path = guard._get_usage_file_path()
        data = json.loads(usage_path.read_text(encoding="utf-8"))

        # Total should only include the new record, not the voided hold
        new_cost = 0.002 * (100 / 1000)  # output_per_1k * tokens/1000
        assert abs(data["total_usd"] - new_cost) < 0.001
        # Verify the voided entry has cost_usd=0
        voided = [e for e in data["entries"] if e.get("hold_id") == "h_budget001"]
        assert voided[0]["cost_usd"] == 0

    def test_custom_timeout_from_config(self, tmp_env):
        """hold_timeout_hours from config should be respected."""
        config_path, data_dir = tmp_env

        # Set a short timeout of 2 hours
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["hold_timeout_hours"] = 2
        config_path.write_text(json.dumps(config), encoding="utf-8")

        # Create a hold that is 3 hours old (older than 2h timeout)
        _inject_hold_entry(data_dir, "h_custom001", 0.10, hours_ago=3)
        # Create a hold that is 1 hour old (younger than 2h timeout)
        _inject_hold_entry(data_dir, "h_custom002", 0.10, hours_ago=1)

        guard = _make_guard(config_path, data_dir)
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        usage_path = guard._get_usage_file_path()
        data = json.loads(usage_path.read_text(encoding="utf-8"))

        old_hold = [e for e in data["entries"] if e.get("hold_id") == "h_custom001"]
        young_hold = [e for e in data["entries"] if e.get("hold_id") == "h_custom002"]

        assert old_hold[0]["status"] == "voided_timeout"
        assert young_hold[0]["status"] == "hold"

    def test_status_shows_voided_entries(self, tmp_env, capsys):
        """banto status should display voided timeout entries."""
        config_path, data_dir = tmp_env
        # Create and void a stale hold
        _inject_hold_entry(data_dir, "h_status001", 0.10, hours_ago=25)
        _inject_hold_entry(data_dir, "h_status002", 0.05, hours_ago=30)

        guard = _make_guard(config_path, data_dir)

        # Trigger voiding via _update_usage
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        # Now check get_remaining_budget
        status = guard.get_remaining_budget()
        assert status["voided_timeout_count"] == 2
        assert abs(status["voided_timeout_usd"] - 0.15) < 0.001

    def test_edge_hold_exactly_at_timeout_not_voided(self, tmp_env):
        """A hold exactly at the timeout boundary should NOT be voided.

        The condition is strictly greater-than, not greater-than-or-equal.
        """
        config_path, data_dir = tmp_env

        # Pick a precise held_at and freeze now to exactly 24h later.
        held_at = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        frozen_now = held_at + timedelta(hours=24)  # exactly 24h = 86400s

        now = datetime.now(timezone.utc)
        usage_path = data_dir / f"usage_{now.year}_{now.month:02d}.json"
        data = {
            "month": f"{now.year}-{now.month:02d}",
            "total_usd": 0.10,
            "entry_count": 1,
            "entries": [{
                "timestamp": held_at.isoformat(timespec="seconds"),
                "model": "test-model",
                "provider": "test",
                "operation": "hold",
                "status": "hold",
                "hold_id": "h_exact24",
                "params": {},
                "cost_usd": 0.10,
                "cumulative_usd": 0.10,
                "caller": "test",
            }],
        }
        usage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        guard = _make_guard(config_path, data_dir)

        # Patch datetime in banto.guard so _void_stale_holds (called by
        # _update_usage internally) sees frozen_now as "now".
        with patch("banto.guard.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_now
            mock_dt.fromisoformat = datetime.fromisoformat

            guard.record_usage(
                model="test-model",
                input_tokens=0,
                output_tokens=100,
                provider="test",
                operation="test",
            )

        data_after = json.loads(usage_path.read_text(encoding="utf-8"))
        edge_entry = [
            e for e in data_after["entries"] if e.get("hold_id") == "h_exact24"
        ]
        assert len(edge_entry) == 1
        # Exactly 24h = 86400 seconds, which is NOT > 86400, so hold remains
        assert edge_entry[0]["status"] == "hold"

    def test_hold_just_over_timeout_voided(self, tmp_env):
        """A hold just over the timeout should be voided."""
        config_path, data_dir = tmp_env

        held_at = datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)
        now_time = datetime.now(timezone.utc)

        usage_path = data_dir / f"usage_{now_time.year}_{now_time.month:02d}.json"
        data = {
            "month": f"{now_time.year}-{now_time.month:02d}",
            "total_usd": 0.10,
            "entry_count": 1,
            "entries": [{
                "timestamp": held_at.isoformat(timespec="seconds"),
                "model": "test-model",
                "provider": "test",
                "operation": "hold",
                "status": "hold",
                "hold_id": "h_over24",
                "params": {},
                "cost_usd": 0.10,
                "cumulative_usd": 0.10,
                "caller": "test",
            }],
        }
        usage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        guard = _make_guard(config_path, data_dir)
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        data_after = json.loads(usage_path.read_text(encoding="utf-8"))
        over_entry = [
            e for e in data_after["entries"] if e.get("hold_id") == "h_over24"
        ]
        assert len(over_entry) == 1
        assert over_entry[0]["status"] == "voided_timeout"

    def test_settled_and_voided_entries_not_affected(self, tmp_env):
        """Entries with status 'settled' or 'voided' should not be touched."""
        config_path, data_dir = tmp_env
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=48)).isoformat(timespec="seconds")

        usage_path = data_dir / f"usage_{now.year}_{now.month:02d}.json"
        data = {
            "month": f"{now.year}-{now.month:02d}",
            "total_usd": 0.30,
            "entry_count": 3,
            "entries": [
                {
                    "timestamp": old_ts,
                    "model": "test-model",
                    "provider": "test",
                    "operation": "hold",
                    "status": "settled",
                    "hold_id": "h_settled",
                    "params": {},
                    "cost_usd": 0.10,
                    "cumulative_usd": 0.10,
                    "caller": "test",
                },
                {
                    "timestamp": old_ts,
                    "model": "test-model",
                    "provider": "test",
                    "operation": "test",
                    "status": "voided_timeout",
                    "hold_id": "h_already_voided",
                    "params": {},
                    "cost_usd": 0,
                    "hold_cost_usd": 0.10,
                    "cumulative_usd": 0.10,
                    "caller": "test",
                },
                {
                    "timestamp": old_ts,
                    "model": "test-model",
                    "provider": "test",
                    "operation": "hold",
                    "status": "hold",
                    "hold_id": "h_stale",
                    "params": {},
                    "cost_usd": 0.10,
                    "cumulative_usd": 0.30,
                    "caller": "test",
                },
            ],
        }
        usage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        guard = _make_guard(config_path, data_dir)
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        data_after = json.loads(usage_path.read_text(encoding="utf-8"))

        settled = [e for e in data_after["entries"] if e.get("hold_id") == "h_settled"]
        assert settled[0]["status"] == "settled"
        assert settled[0]["cost_usd"] == 0.10

        already_voided = [
            e for e in data_after["entries"]
            if e.get("hold_id") == "h_already_voided"
        ]
        assert already_voided[0]["status"] == "voided_timeout"

        stale = [e for e in data_after["entries"] if e.get("hold_id") == "h_stale"]
        assert stale[0]["status"] == "voided_timeout"
        assert stale[0]["cost_usd"] == 0

    def test_voiding_during_hold_budget(self, tmp_env):
        """Stale holds should be voided even when hold_budget triggers _update_usage."""
        config_path, data_dir = tmp_env
        _inject_hold_entry(data_dir, "h_stale_before_hold", 5.0, hours_ago=25)

        guard = _make_guard(config_path, data_dir)
        # hold_budget should void the stale hold, freeing budget
        hold_id = guard.hold_budget(
            model="test-model",
            input_tokens=0,
            output_tokens=1000,
        )

        usage_path = guard._get_usage_file_path()
        data = json.loads(usage_path.read_text(encoding="utf-8"))

        old_hold = [
            e for e in data["entries"]
            if e.get("hold_id") == "h_stale_before_hold"
        ]
        assert old_hold[0]["status"] == "voided_timeout"
        assert old_hold[0]["cost_usd"] == 0

        new_hold = [e for e in data["entries"] if e.get("hold_id") == hold_id]
        assert len(new_hold) == 1
        assert new_hold[0]["status"] == "hold"

    def test_default_timeout_24h_when_not_in_config(self, tmp_env):
        """When hold_timeout_hours is not in config, default to 24."""
        config_path, data_dir = tmp_env
        config = json.loads(config_path.read_text(encoding="utf-8"))
        del config["hold_timeout_hours"]
        config_path.write_text(json.dumps(config), encoding="utf-8")

        guard = _make_guard(config_path, data_dir)
        assert guard.hold_timeout_hours == 24

        # A 25h old hold should still be voided with default timeout
        _inject_hold_entry(data_dir, "h_default001", 0.10, hours_ago=25)
        guard.record_usage(
            model="test-model",
            input_tokens=0,
            output_tokens=100,
            provider="test",
            operation="test",
        )

        usage_path = guard._get_usage_file_path()
        data = json.loads(usage_path.read_text(encoding="utf-8"))
        hold = [e for e in data["entries"] if e.get("hold_id") == "h_default001"]
        assert hold[0]["status"] == "voided_timeout"
