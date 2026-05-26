from datetime import datetime, timezone
from typing import Any

from app.models import JobRun, JobStatus, KeyAttempt, KeyStatus, WarpKey


def apply_attempt_result(run: JobRun, key: WarpKey, result: dict[str, Any]) -> KeyAttempt:
    status = str(result.get("status", "unknown"))
    license_step_reached = bool(result.get("license_step_reached", False))
    reason = result.get("reason")
    now = datetime.now(timezone.utc)
    if key.status is None:
        key.status = KeyStatus.valid
    if run.host.ready is None:
        run.host.ready = False

    attempt = KeyAttempt(
        run=run,
        warp_key=key,
        status=status,
        license_step_reached=license_step_reached,
        reason=str(reason) if reason is not None else None,
        raw_output=result.get("raw_output"),
    )
    run.attempts.append(attempt)
    key.last_used_at = now

    if status == "license_applied":
        run.status = JobStatus.succeeded
        run.summary = reason or "license applied"
        run.finished_at = now
        run.host.ready = True
        run.host.last_ready_at = now
        return attempt

    if license_step_reached:
        key.status = KeyStatus.invalid
        key.invalidated_at = now

    return attempt
