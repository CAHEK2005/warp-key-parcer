from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from app.config import get_settings
from app.db import make_session_factory
from app.models import HostSchedule, JobRun, JobStatus, ScheduleKind
from app.tasks import apply_warp_key


def enqueue_schedule(schedule_id: int) -> None:
    settings = get_settings()
    session_factory = make_session_factory(settings.database_url)
    with session_factory() as session:
        schedule = session.get(HostSchedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        run = JobRun(host_id=schedule.host_id, schedule_id=schedule.id, trigger="schedule", status=JobStatus.pending)
        session.add(run)
        session.commit()
        apply_warp_key.delay(run.id)


def build_scheduler() -> BlockingScheduler:
    settings = get_settings()
    session_factory = make_session_factory(settings.database_url)
    scheduler = BlockingScheduler(timezone=settings.timezone)
    with session_factory() as session:
        for schedule in session.scalars(select(HostSchedule).where(HostSchedule.enabled.is_(True))).all():
            if schedule.kind == ScheduleKind.one_time and schedule.run_at:
                scheduler.add_job(enqueue_schedule, "date", run_date=schedule.run_at, args=[schedule.id], id=f"schedule-{schedule.id}")
            elif schedule.kind == ScheduleKind.interval and schedule.interval_seconds:
                scheduler.add_job(
                    enqueue_schedule,
                    "interval",
                    seconds=schedule.interval_seconds,
                    args=[schedule.id],
                    id=f"schedule-{schedule.id}",
                )
            elif schedule.kind == ScheduleKind.cron and schedule.cron:
                minute, hour, day, month, day_of_week = schedule.cron.split()
                scheduler.add_job(
                    enqueue_schedule,
                    "cron",
                    minute=minute,
                    hour=hour,
                    day=day,
                    month=month,
                    day_of_week=day_of_week,
                    args=[schedule.id],
                    id=f"schedule-{schedule.id}",
                )
    return scheduler


if __name__ == "__main__":
    build_scheduler().start()
