from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import init_db, make_session_factory, session_dependency
from app.domain.keys import DEFAULT_WARP_KEY_REGEX, fingerprint_secret, normalize_regex_pattern
from app.domain.schedules import ScheduleValidationError, validate_schedule
from app.models import (
    AdminUser,
    Host,
    HostSchedule,
    JobRun,
    JobStatus,
    KeyStatus,
    ScheduleKind,
    SshKey,
    TelegramSource,
    WarpKey,
    KeyAttempt,
)
from app.schemas import (
    AdminResponse,
    HostCreate,
    HostResponse,
    JobRunResponse,
    LoginRequest,
    ScheduleCreate,
    ScheduleResponse,
    SshKeyCreate,
    SshKeyResponse,
    TelegramSourceCreate,
    TelegramSourceResponse,
    TokenResponse,
    WarpKeyCreate,
    WarpKeyResponse,
)
from app.security import create_access_token, decode_access_token, decrypt_secret, encrypt_secret, hash_password, verify_password


def _as_ssh_key_response(item: SshKey, private_key: str | None = None) -> SshKeyResponse:
    return SshKeyResponse(id=item.id, name=item.name, fingerprint=item.fingerprint, tail=item.tail, private_key=private_key)


def _as_host_response(item: Host) -> HostResponse:
    return HostResponse(
        id=item.id,
        name=item.name,
        address=item.address,
        ssh_username=item.ssh_username,
        ssh_port=item.ssh_port,
        ssh_key_id=item.ssh_key_id,
        ready=item.ready,
        last_ready_at=item.last_ready_at,
    )


def _as_telegram_source_response(item: TelegramSource) -> TelegramSourceResponse:
    return TelegramSourceResponse(
        id=item.id,
        name=item.name,
        access_mode=item.access_mode,
        channel_ref=item.channel_ref,
        regex=item.regex,
        enabled=item.enabled,
        last_sync_at=item.last_sync_at,
    )


def _as_warp_key_response(item: WarpKey, value: str | None = None) -> WarpKeyResponse:
    return WarpKeyResponse(
        id=item.id,
        fingerprint=item.fingerprint,
        tail=item.tail,
        status=item.status,
        source_id=item.source_id,
        created_at=item.created_at,
        value=value,
    )


def _as_schedule_response(item: HostSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=item.id,
        host_id=item.host_id,
        kind=item.kind,
        run_at=item.run_at,
        cron=item.cron,
        interval_seconds=item.interval_seconds,
        timezone=item.timezone,
        enabled=item.enabled,
    )


def _as_job_response(item: JobRun) -> JobRunResponse:
    return JobRunResponse(
        id=item.id,
        host_id=item.host_id,
        schedule_id=item.schedule_id,
        trigger=item.trigger,
        status=item.status,
        summary=item.summary,
        created_at=item.created_at,
        started_at=item.started_at,
        finished_at=item.finished_at,
    )


def _create_default_admin(session_factory: sessionmaker[Session], username: str, password: str) -> None:
    with session_factory() as session:
        existing = session.scalar(select(AdminUser).where(AdminUser.username == username))
        if existing is None:
            session.add(AdminUser(username=username, password_hash=hash_password(password)))
            session.commit()
            return
        if not verify_password(password, existing.password_hash):
            existing.password_hash = hash_password(password)
            session.commit()


def create_app(
    database_url: str | None = None,
    secret_key: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    settings = get_settings()
    active_database_url = database_url or settings.database_url
    active_secret_key = secret_key or settings.secret_key
    local_session_factory = session_factory or make_session_factory(active_database_url)
    init_db(local_session_factory)
    _create_default_admin(local_session_factory, settings.admin_username, settings.admin_password)

    app = FastAPI(title="WARP+ Key Orchestrator", version="0.1.0")
    get_session = session_dependency(local_session_factory)

    def current_admin(
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> AdminUser:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
        try:
            username = decode_access_token(active_secret_key, authorization.removeprefix("Bearer ").strip())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
        admin = session.scalar(select(AdminUser).where(AdminUser.username == username))
        if admin is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown admin")
        return admin

    AuthDep = Depends(current_admin)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login", response_model=TokenResponse)
    def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
        admin = session.scalar(select(AdminUser).where(AdminUser.username == payload.username))
        if admin is None or not verify_password(payload.password, admin.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad credentials")
        return TokenResponse(
            access_token=create_access_token(active_secret_key, admin.username, settings.access_token_minutes),
        )

    @app.get("/auth/me", response_model=AdminResponse)
    def me(admin: AdminUser = AuthDep) -> AdminResponse:
        return AdminResponse(username=admin.username)

    @app.post("/ssh-keys", response_model=SshKeyResponse, status_code=201)
    def create_ssh_key(payload: SshKeyCreate, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> SshKeyResponse:
        item = SshKey(
            name=payload.name,
            encrypted_private_key=encrypt_secret(active_secret_key, payload.private_key) or "",
            encrypted_passphrase=encrypt_secret(active_secret_key, payload.passphrase),
            fingerprint=fingerprint_secret(payload.private_key),
            tail=payload.private_key[-4:],
        )
        session.add(item)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="ssh key already exists") from exc
        session.refresh(item)
        return _as_ssh_key_response(item)

    @app.get("/ssh-keys", response_model=list[SshKeyResponse])
    def list_ssh_keys(_: AdminUser = AuthDep, session: Session = Depends(get_session)) -> list[SshKeyResponse]:
        return [_as_ssh_key_response(item) for item in session.scalars(select(SshKey).order_by(SshKey.name)).all()]

    @app.get("/ssh-keys/{ssh_key_id}/reveal", response_model=SshKeyResponse)
    def reveal_ssh_key(ssh_key_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> SshKeyResponse:
        item = session.get(SshKey, ssh_key_id)
        if item is None:
            raise HTTPException(status_code=404, detail="ssh key not found")
        return _as_ssh_key_response(item, private_key=decrypt_secret(active_secret_key, item.encrypted_private_key))

    @app.delete("/ssh-keys/{ssh_key_id}", status_code=204)
    def delete_ssh_key(ssh_key_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> None:
        item = session.get(SshKey, ssh_key_id)
        if item is None:
            raise HTTPException(status_code=404, detail="ssh key not found")
        if session.scalar(select(Host).where(Host.ssh_key_id == ssh_key_id)) is not None:
            raise HTTPException(status_code=409, detail="ssh key is assigned to one or more hosts")
        session.delete(item)
        session.commit()

    @app.post("/hosts", response_model=HostResponse, status_code=201)
    def create_host(payload: HostCreate, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> HostResponse:
        if payload.ssh_key_id is not None and session.get(SshKey, payload.ssh_key_id) is None:
            raise HTTPException(status_code=400, detail="ssh key not found")
        item = Host(**payload.model_dump())
        session.add(item)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="host already exists") from exc
        session.refresh(item)
        return _as_host_response(item)

    @app.get("/hosts", response_model=list[HostResponse])
    def list_hosts(_: AdminUser = AuthDep, session: Session = Depends(get_session)) -> list[HostResponse]:
        return [_as_host_response(item) for item in session.scalars(select(Host).order_by(Host.name)).all()]

    @app.delete("/hosts/{host_id}", status_code=204)
    def delete_host(host_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> None:
        item = session.get(Host, host_id)
        if item is None:
            raise HTTPException(status_code=404, detail="host not found")
        for run in session.scalars(select(JobRun).where(JobRun.host_id == host_id)).all():
            session.delete(run)
        for schedule in session.scalars(select(HostSchedule).where(HostSchedule.host_id == host_id)).all():
            session.delete(schedule)
        session.delete(item)
        session.commit()

    @app.post("/hosts/{host_id}/test-connection")
    def test_host_connection(host_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> dict[str, str]:
        host = session.get(Host, host_id)
        if host is None:
            raise HTTPException(status_code=404, detail="host not found")
        if host.ssh_key_id is None:
            raise HTTPException(status_code=400, detail="host has no ssh key")
        return {"status": "configured", "host": host.name}

    @app.post("/telegram-sources", response_model=TelegramSourceResponse, status_code=201)
    def create_telegram_source(
        payload: TelegramSourceCreate,
        _: AdminUser = AuthDep,
        session: Session = Depends(get_session),
    ) -> TelegramSourceResponse:
        item = TelegramSource(
            name=payload.name,
            access_mode=payload.access_mode,
            channel_ref=payload.channel_ref,
            regex=normalize_regex_pattern(payload.regex or DEFAULT_WARP_KEY_REGEX),
            encrypted_secret=encrypt_secret(active_secret_key, payload.secret),
            enabled=payload.enabled,
        )
        session.add(item)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="telegram source already exists") from exc
        session.refresh(item)
        return _as_telegram_source_response(item)

    @app.get("/telegram-sources", response_model=list[TelegramSourceResponse])
    def list_telegram_sources(_: AdminUser = AuthDep, session: Session = Depends(get_session)) -> list[TelegramSourceResponse]:
        return [_as_telegram_source_response(item) for item in session.scalars(select(TelegramSource).order_by(TelegramSource.name)).all()]

    @app.post("/telegram-sources/{source_id}/sync")
    def sync_telegram_source(source_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> dict[str, str | int]:
        if session.get(TelegramSource, source_id) is None:
            raise HTTPException(status_code=404, detail="telegram source not found")
        try:
            from app.tasks import parse_telegram_source

            parse_telegram_source.delay(source_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="worker queue unavailable") from exc
        return {"status": "queued", "source_id": source_id}

    @app.delete("/telegram-sources/{source_id}", status_code=204)
    def delete_telegram_source(source_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> None:
        item = session.get(TelegramSource, source_id)
        if item is None:
            raise HTTPException(status_code=404, detail="telegram source not found")
        for key in session.scalars(select(WarpKey).where(WarpKey.source_id == source_id)).all():
            key.source_id = None
        session.delete(item)
        session.commit()

    @app.post("/warp-keys", response_model=WarpKeyResponse, status_code=201)
    def create_warp_key(payload: WarpKeyCreate, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> WarpKeyResponse:
        fingerprint = fingerprint_secret(payload.value)
        existing = session.scalar(select(WarpKey).where(WarpKey.fingerprint == fingerprint))
        if existing is not None:
            return _as_warp_key_response(existing)
        item = WarpKey(
            encrypted_value=encrypt_secret(active_secret_key, payload.value) or "",
            fingerprint=fingerprint,
            tail=payload.value[-4:],
            source_id=payload.source_id,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return _as_warp_key_response(item)

    @app.get("/warp-keys", response_model=list[WarpKeyResponse])
    def list_warp_keys(
        status_filter: KeyStatus | None = None,
        _: AdminUser = AuthDep,
        session: Session = Depends(get_session),
    ) -> list[WarpKeyResponse]:
        query = select(WarpKey).order_by(WarpKey.created_at.desc())
        if status_filter is not None:
            query = query.where(WarpKey.status == status_filter)
        return [_as_warp_key_response(item) for item in session.scalars(query).all()]

    @app.get("/warp-keys/{warp_key_id}/reveal", response_model=WarpKeyResponse)
    def reveal_warp_key(warp_key_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> WarpKeyResponse:
        item = session.get(WarpKey, warp_key_id)
        if item is None:
            raise HTTPException(status_code=404, detail="warp key not found")
        return _as_warp_key_response(item, value=decrypt_secret(active_secret_key, item.encrypted_value))

    def set_warp_key_status(warp_key_id: int, key_status: KeyStatus, session: Session) -> WarpKeyResponse:
        item = session.get(WarpKey, warp_key_id)
        if item is None:
            raise HTTPException(status_code=404, detail="warp key not found")
        item.status = key_status
        item.invalidated_at = datetime.now(timezone.utc) if key_status == KeyStatus.invalid else None
        session.commit()
        session.refresh(item)
        return _as_warp_key_response(item)

    @app.post("/warp-keys/{warp_key_id}/invalidate", response_model=WarpKeyResponse)
    def invalidate_warp_key(warp_key_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> WarpKeyResponse:
        return set_warp_key_status(warp_key_id, KeyStatus.invalid, session)

    @app.post("/warp-keys/{warp_key_id}/reactivate", response_model=WarpKeyResponse)
    def reactivate_warp_key(warp_key_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> WarpKeyResponse:
        return set_warp_key_status(warp_key_id, KeyStatus.valid, session)

    @app.delete("/warp-keys/{warp_key_id}", status_code=204)
    def delete_warp_key(warp_key_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> None:
        item = session.get(WarpKey, warp_key_id)
        if item is None:
            raise HTTPException(status_code=404, detail="warp key not found")
        for attempt in session.scalars(select(KeyAttempt).where(KeyAttempt.warp_key_id == warp_key_id)).all():
            session.delete(attempt)
        session.delete(item)
        session.commit()

    @app.post("/schedules", response_model=ScheduleResponse, status_code=201)
    def create_schedule(payload: ScheduleCreate, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> ScheduleResponse:
        if session.get(Host, payload.host_id) is None:
            raise HTTPException(status_code=400, detail="host not found")
        try:
            shape = validate_schedule(
                payload.kind.value,
                run_at=payload.run_at,
                cron=payload.cron,
                interval_seconds=payload.interval_seconds,
            )
        except ScheduleValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        item = HostSchedule(
            host_id=payload.host_id,
            kind=ScheduleKind(str(shape["kind"])),
            run_at=shape["run_at"],
            cron=shape["cron"],
            interval_seconds=shape["interval_seconds"],
            timezone=payload.timezone,
            enabled=payload.enabled,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return _as_schedule_response(item)

    @app.get("/schedules", response_model=list[ScheduleResponse])
    def list_schedules(_: AdminUser = AuthDep, session: Session = Depends(get_session)) -> list[ScheduleResponse]:
        return [_as_schedule_response(item) for item in session.scalars(select(HostSchedule).order_by(HostSchedule.id.desc())).all()]

    @app.delete("/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> None:
        item = session.get(HostSchedule, schedule_id)
        if item is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        session.delete(item)
        session.commit()

    @app.post("/jobs/hosts/{host_id}/run", response_model=JobRunResponse, status_code=202)
    def trigger_host_run(host_id: int, _: AdminUser = AuthDep, session: Session = Depends(get_session)) -> JobRunResponse:
        if session.get(Host, host_id) is None:
            raise HTTPException(status_code=404, detail="host not found")
        item = JobRun(host_id=host_id, trigger="manual", status=JobStatus.pending)
        session.add(item)
        session.commit()
        session.refresh(item)
        try:
            from app.tasks import apply_warp_key

            apply_warp_key.delay(item.id)
        except Exception as exc:
            item.status = JobStatus.failed
            item.summary = "failed to enqueue worker task"
            item.finished_at = datetime.now(timezone.utc)
            session.commit()
            raise HTTPException(status_code=503, detail="worker queue unavailable") from exc
        return _as_job_response(item)

    @app.get("/jobs", response_model=list[JobRunResponse])
    def list_jobs(_: AdminUser = AuthDep, session: Session = Depends(get_session)) -> list[JobRunResponse]:
        return [_as_job_response(item) for item in session.scalars(select(JobRun).order_by(JobRun.created_at.desc())).all()]

    app.state.session_factory = local_session_factory
    app.state.secret_key = active_secret_key
    return app


app = create_app()
