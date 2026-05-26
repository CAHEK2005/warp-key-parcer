from datetime import datetime, timezone
import asyncio

from celery import Celery
from sqlalchemy import select

from app.config import get_settings
from app.db import make_session_factory
from app.domain.results import apply_attempt_result
from app.models import Host, JobRun, JobStatus, KeyStatus, SshKey, TelegramSource, WarpKey
from app.remote.ansible_runner import run_warp_playbook
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
        if host is None or host.ssh_key_id is None:
            run.status = JobStatus.failed
            run.summary = "host or ssh key missing"
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

        run.status = JobStatus.running
        run.started_at = datetime.now(timezone.utc)
        session.commit()

        private_key = decrypt_secret(settings.secret_key, ssh_key.encrypted_private_key) or ""
        keys = session.scalars(select(WarpKey).where(WarpKey.status == KeyStatus.valid).order_by(WarpKey.created_at)).all()
        for key in keys:
            license_key = decrypt_secret(settings.secret_key, key.encrypted_value) or ""
            result = run_warp_playbook(host, private_key, license_key, settings.ansible_private_data_dir)
            apply_attempt_result(run, key, result)
            session.commit()
            if result.get("status") == "license_applied":
                return {"status": "succeeded", "reason": "license applied"}

        run.status = JobStatus.exhausted
        run.summary = "no valid WARP keys worked"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return {"status": "exhausted", "reason": run.summary}
