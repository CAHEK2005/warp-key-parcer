from datetime import datetime


class ScheduleValidationError(ValueError):
    pass


def validate_schedule(
    kind: str,
    *,
    run_at: datetime | None,
    cron: str | None,
    interval_seconds: int | None,
) -> dict[str, object]:
    if kind == "one_time":
        if run_at is None:
            raise ScheduleValidationError("one_time schedules require run_at")
        return {"kind": kind, "run_at": run_at, "cron": None, "interval_seconds": None}
    if kind == "cron":
        if not cron or len(cron.split()) != 5:
            raise ScheduleValidationError("cron schedules require a five-field cron expression")
        return {"kind": kind, "run_at": None, "cron": cron, "interval_seconds": None}
    if kind == "interval":
        if interval_seconds is None or interval_seconds < 60:
            raise ScheduleValidationError("interval schedules require interval_seconds >= 60")
        return {"kind": kind, "run_at": None, "cron": None, "interval_seconds": interval_seconds}
    raise ScheduleValidationError(f"unsupported schedule kind: {kind}")
