import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class KeyStatus(str, enum.Enum):
    valid = "valid"
    invalid = "invalid"
    suspended = "suspended"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    exhausted = "exhausted"


class TelegramAccessMode(str, enum.Enum):
    bot = "bot"
    user_session = "user_session"


class ScheduleKind(str, enum.Enum):
    one_time = "one_time"
    cron = "cron"
    interval = "interval"


class HostAuthMode(str, enum.Enum):
    key = "key"
    password = "password"


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TelegramSource(Base):
    __tablename__ = "telegram_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    access_mode: Mapped[TelegramAccessMode] = mapped_column(Enum(TelegramAccessMode), default=TelegramAccessMode.bot)
    channel_ref: Mapped[str] = mapped_column(String(255))
    regex: Mapped[str] = mapped_column(Text)
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WarpKey(Base):
    __tablename__ = "warp_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(90), unique=True, index=True)
    tail: Mapped[str] = mapped_column(String(16))
    status: Mapped[KeyStatus] = mapped_column(Enum(KeyStatus), default=KeyStatus.valid, index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_sources.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped[TelegramSource | None] = relationship()


class SshKey(Base):
    __tablename__ = "ssh_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    encrypted_private_key: Mapped[str] = mapped_column(Text)
    encrypted_passphrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(90))
    tail: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    address: Mapped[str] = mapped_column(String(255))
    ssh_username: Mapped[str] = mapped_column(String(120), default="root")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    auth_mode: Mapped[HostAuthMode] = mapped_column(Enum(HostAuthMode), default=HostAuthMode.key)
    ssh_key_id: Mapped[int | None] = mapped_column(ForeignKey("ssh_keys.id"), nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ssh_key: Mapped[SshKey | None] = relationship()


class HostSchedule(Base):
    __tablename__ = "host_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id"))
    kind: Mapped[ScheduleKind] = mapped_column(Enum(ScheduleKind))
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/Moscow")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    host: Mapped[Host] = relationship()


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id"))
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("host_schedules.id"), nullable=True)
    trigger: Mapped[str] = mapped_column(String(80))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    host: Mapped[Host] = relationship()
    schedule: Mapped[HostSchedule | None] = relationship()
    attempts: Mapped[list["KeyAttempt"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class KeyAttempt(Base):
    __tablename__ = "key_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id"))
    warp_key_id: Mapped[int] = mapped_column(ForeignKey("warp_keys.id"))
    status: Mapped[str] = mapped_column(String(80))
    license_step_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[JobRun] = relationship(back_populates="attempts")
    warp_key: Mapped[WarpKey] = relationship()
