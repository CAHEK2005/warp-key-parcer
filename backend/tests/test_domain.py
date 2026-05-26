from datetime import datetime, timezone

import pytest

from app.domain.keys import DEFAULT_WARP_KEY_REGEX, extract_warp_keys, fingerprint_secret
from app.domain.results import apply_attempt_result
from app.domain.schedules import ScheduleValidationError, validate_schedule
from app.models import Host, JobRun, KeyStatus, WarpKey


def test_extract_warp_keys_uses_configurable_regex_and_deduplicates() -> None:
    text = """
    first: 12345678-1234-1234-1234-123456789abc
    repeated 12345678-1234-1234-1234-123456789ABC
    custom KEY-42
    """

    keys = extract_warp_keys(
        text,
        rf"{DEFAULT_WARP_KEY_REGEX}|KEY-\d+",
    )

    assert keys == [
        "12345678-1234-1234-1234-123456789abc",
        "KEY-42",
    ]


def test_fingerprint_secret_never_returns_full_secret() -> None:
    fingerprint = fingerprint_secret("12345678-1234-1234-1234-123456789abc")

    assert fingerprint.startswith("sha256:")
    assert fingerprint.endswith(":9abc")
    assert "12345678-1234" not in fingerprint


def test_invalidates_key_only_when_license_step_was_reached() -> None:
    key = WarpKey(encrypted_value="encrypted", fingerprint="fp", tail="9abc")
    host = Host(name="edge-1", address="192.0.2.10", ssh_username="root")
    run = JobRun(host=host, trigger="manual")

    apply_attempt_result(
        run=run,
        key=key,
        result={
            "status": "ssh_failed",
            "license_step_reached": False,
            "reason": "Permission denied",
        },
    )

    assert key.status == KeyStatus.valid
    assert host.ready is False

    apply_attempt_result(
        run=run,
        key=key,
        result={
            "status": "license_failed",
            "license_step_reached": True,
            "reason": "license rejected",
        },
    )

    assert key.status == KeyStatus.invalid
    assert host.ready is False


def test_successful_license_marks_host_ready_without_consuming_key() -> None:
    key = WarpKey(encrypted_value="encrypted", fingerprint="fp", tail="9abc")
    host = Host(name="edge-1", address="192.0.2.10", ssh_username="root")
    run = JobRun(host=host, trigger="manual")

    apply_attempt_result(
        run=run,
        key=key,
        result={
            "status": "license_applied",
            "license_step_reached": True,
            "reason": "warp plus active",
        },
    )

    assert key.status == KeyStatus.valid
    assert host.ready is True
    assert host.last_ready_at is not None


def test_validate_schedule_accepts_one_time_cron_and_interval() -> None:
    at = datetime(2026, 6, 1, 10, 30, tzinfo=timezone.utc)

    assert validate_schedule("one_time", run_at=at, cron=None, interval_seconds=None) == {
        "kind": "one_time",
        "run_at": at,
        "cron": None,
        "interval_seconds": None,
    }
    assert validate_schedule("cron", run_at=None, cron="0 4 * * *", interval_seconds=None)["cron"] == "0 4 * * *"
    assert validate_schedule("interval", run_at=None, cron=None, interval_seconds=3600)["interval_seconds"] == 3600


@pytest.mark.parametrize(
    ("kind", "run_at", "cron", "interval_seconds"),
    [
        ("one_time", None, None, None),
        ("cron", None, "* * *", None),
        ("interval", None, None, 0),
        ("unknown", None, None, None),
    ],
)
def test_validate_schedule_rejects_invalid_shapes(kind, run_at, cron, interval_seconds) -> None:
    with pytest.raises(ScheduleValidationError):
        validate_schedule(kind, run_at=run_at, cron=cron, interval_seconds=interval_seconds)
