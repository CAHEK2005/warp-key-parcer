from datetime import datetime

from pydantic import BaseModel, Field

from app.models import JobStatus, KeyStatus, ScheduleKind, TelegramAccessMode


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminResponse(BaseModel):
    username: str


class SshKeyCreate(BaseModel):
    name: str
    private_key: str
    passphrase: str | None = None


class SshKeyResponse(BaseModel):
    id: int
    name: str
    fingerprint: str
    tail: str
    private_key: str | None = None


class HostCreate(BaseModel):
    name: str
    address: str
    ssh_username: str = "root"
    ssh_port: int = 22
    ssh_key_id: int | None = None


class HostResponse(BaseModel):
    id: int
    name: str
    address: str
    ssh_username: str
    ssh_port: int
    ssh_key_id: int | None
    ready: bool
    last_ready_at: datetime | None


class TelegramSourceCreate(BaseModel):
    name: str
    access_mode: TelegramAccessMode
    channel_ref: str
    regex: str
    secret: str | None = None
    enabled: bool = True


class TelegramSourceResponse(BaseModel):
    id: int
    name: str
    access_mode: TelegramAccessMode
    channel_ref: str
    regex: str
    enabled: bool
    last_sync_at: datetime | None


class WarpKeyCreate(BaseModel):
    value: str
    source_id: int | None = None


class WarpKeyResponse(BaseModel):
    id: int
    fingerprint: str
    tail: str
    status: KeyStatus
    source_id: int | None
    created_at: datetime
    value: str | None = None


class ScheduleCreate(BaseModel):
    host_id: int
    kind: ScheduleKind
    run_at: datetime | None = None
    cron: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    timezone: str = "Europe/Moscow"
    enabled: bool = True


class ScheduleResponse(BaseModel):
    id: int
    host_id: int
    kind: ScheduleKind
    run_at: datetime | None
    cron: str | None
    interval_seconds: int | None
    timezone: str
    enabled: bool


class JobRunResponse(BaseModel):
    id: int
    host_id: int
    schedule_id: int | None
    trigger: str
    status: JobStatus
    summary: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
