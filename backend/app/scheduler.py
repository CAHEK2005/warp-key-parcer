from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker, Session

from app.config import get_settings
from app.db import make_session_factory
from app.models import HostSchedule, JobRun, JobStatus, ScheduleKind


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
        from app.tasks import apply_warp_key

        apply_warp_key.delay(run.id)


def schedule_job_id(schedule_id: int) -> str:
    return f"schedule-{schedule_id}"


def add_schedule_job(scheduler: BaseScheduler, schedule: HostSchedule) -> None:
    job_id = schedule_job_id(schedule.id)
    if scheduler.get_job(job_id) is not None:
        return
    if schedule.kind == ScheduleKind.one_time and schedule.run_at:
        scheduler.add_job(enqueue_schedule, "date", run_date=schedule.run_at, args=[schedule.id], id=job_id)
    elif schedule.kind == ScheduleKind.interval and schedule.interval_seconds:
        scheduler.add_job(
            enqueue_schedule,
            "interval",
            seconds=schedule.interval_seconds,
            args=[schedule.id],
            id=job_id,
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
            id=job_id,
        )


def sync_schedules(scheduler: BaseScheduler, session_factory: sessionmaker[Session] | None = None) -> None:
    settings = get_settings()
    active_session_factory = session_factory or make_session_factory(settings.database_url)
    active_job_ids: set[str] = set()
    with active_session_factory() as session:
        schedules = session.scalars(select(HostSchedule).where(HostSchedule.enabled.is_(True))).all()
        for schedule in schedules:
            job_id = schedule_job_id(schedule.id)
            active_job_ids.add(job_id)
            add_schedule_job(scheduler, schedule)

    for job in scheduler.get_jobs():
        if job.id.startswith("schedule-") and job.id not in active_job_ids:
            scheduler.remove_job(job.id)


def build_scheduler() -> BlockingScheduler:
    settings = get_settings()
    session_factory = make_session_factory(settings.database_url)
    scheduler = BlockingScheduler(timezone=settings.timezone)
    sync_schedules(scheduler, session_factory)
    scheduler.add_job(lambda: sync_schedules(scheduler, session_factory), "interval", seconds=30, id="sync-schedules", replace_existing=True)
    return scheduler


if __name__ == "__main__":
    build_scheduler().start()
