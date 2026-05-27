from datetime import datetime, timezone
import asyncio

from celery import Celery
from sqlalchemy import select

from app.config import get_settings
from app.db import make_session_factory
from app.domain.results import apply_attempt_result
from app.models import Host, HostAuthMode, JobRun, JobStatus, KeyStatus, SshKey, TelegramSource, WarpKey
from app.remote.ansible_runner import run_warp_playbook, run_warp_restore_latest
from app.services.telegram import fetch_telegram_messages, ingest_messages
from app.security import decrypt_secret

settings = get_settings()
celery_app = Celery("warp_orchestrator", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="parse_telegram_source")
def parse_telegram_source(source_id: int) -> dict[str, int]:
    session_factory = make_session_factory(settings.database_url)
    with session_factory() as session:
        source = session.get(TelegramSource, source_id)
        if source is None or not source.enabled:
            return {"source_id": source_id, "created": 0}
        secret = decrypt_secret(settings.secret_key, source.encrypted_secret)
        messages = asyncio.run(fetch_telegram_messages(source, secret))
        created = ingest_messages(session, source, messages, settings.secret_key)
        return {"source_id": source_id, "created": created}


@celery_app.task(name="apply_warp_key")
def apply_warp_key(job_run_id: int) -> dict[str, str]:
    session_factory = make_session_factory(settings.database_url)
    with session_factory() as session:
        run = session.get(JobRun, job_run_id)
        if run is None:
            return {"status": "failed", "reason": "job not found"}
        host = session.get(Host, run.host_id)
        if host is None:
            run.status = JobStatus.failed
            run.summary = "host missing"
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            return {"status": "failed", "reason": run.summary}
        private_key: str | None = None
        password: str | None = None
        if (host.auth_mode or HostAuthMode.key) == HostAuthMode.password:
            password = decrypt_secret(settings.secret_key, host.encrypted_password)
            if not password:
                run.status = JobStatus.failed
                run.summary = "ssh password missing"
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
                return {"status": "failed", "reason": run.summary}
        else:
            if host.ssh_key_id is None:
                run.status = JobStatus.failed
                run.summary = "ssh key missing"
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
                return {"status": "failed", "reason": run.summary}
            ssh_key = session.get(SshKey, host.ssh_key_id)
            if ssh_key is None:
                run.status = JobStatus.failed
                run.summary = "ssh key missing"
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
                return {"status": "failed", "reason": run.summary}
            private_key = decrypt_secret(settings.secret_key, ssh_key.encrypted_private_key) or ""

        if private_key is None and password is None:
            run.status = JobStatus.failed
            run.summary = "ssh credentials missing"
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            return {"status": "failed", "reason": run.summary}

        run.status = JobStatus.running
        run.started_at = datetime.now(timezone.utc)
        session.commit()

        keys = session.scalars(select(WarpKey).where(WarpKey.status == KeyStatus.valid).order_by(WarpKey.created_at)).all()
        attempted = False
        for key in keys:
            attempted = True
            license_key = decrypt_secret(settings.secret_key, key.encrypted_value) or ""
            result = run_warp_playbook(
                host,
                license_key,
                settings.ansible_private_data_dir,
                private_key=private_key,
                password=password,
            )
            apply_attempt_result(run, key, result)
            session.commit()
            if result.get("status") == "license_applied":
                return {"status": "succeeded", "reason": "license applied"}

        restore_result = None
        if attempted:
            restore_result = run_warp_restore_latest(
                host,
                settings.ansible_private_data_dir,
                private_key=private_key,
                password=password,
            )
        run.status = JobStatus.exhausted
        if restore_result is None:
            run.summary = "no valid WARP keys available"
        elif restore_result.get("valid") is True:
            run.summary = "no new WARP keys worked; previous account restored and validated"
        else:
            run.summary = f"no new WARP keys worked; previous account restore check failed: {restore_result.get('reason', 'unknown')}"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return {"status": "exhausted", "reason": run.summary}
