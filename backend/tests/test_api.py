from fastapi.testclient import TestClient
import sys
from types import ModuleType

from app.db import init_db, make_session_factory
from app.main import _create_default_admin, create_app
from app.models import AdminUser, Host, HostAuthMode, HostSchedule, JobRun, KeyStatus, ScheduleKind, SshKey, TelegramSource, WarpKey
from app.security import encrypt_secret, verify_password
from app.scheduler import sync_schedules
from apscheduler.schedulers.blocking import BlockingScheduler


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_auth_current_user() -> None:
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret"))

    headers = auth_headers(client)
    response = client.get("/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_admin_bootstrap_updates_existing_password_from_environment() -> None:
    session_factory = make_session_factory("sqlite:///:memory:")
    init_db(session_factory)

    _create_default_admin(session_factory, "admin", "old-password")
    _create_default_admin(session_factory, "admin", "new-password")

    with session_factory() as session:
        admin = session.query(AdminUser).filter_by(username="admin").one()

    assert verify_password("new-password", admin.password_hash)
    assert not verify_password("old-password", admin.password_hash)


def test_secret_lifecycle_masks_and_reveals_ssh_key() -> None:
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret"))
    headers = auth_headers(client)

    created = client.post(
        "/ssh-keys",
        headers=headers,
        json={
            "name": "prod-root",
            "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
            "passphrase": "optional",
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["private_key"] is None
    assert body["fingerprint"].startswith("sha256:")

    revealed = client.get(f"/ssh-keys/{body['id']}/reveal", headers=headers)

    assert revealed.status_code == 200
    assert revealed.json()["private_key"].startswith("-----BEGIN PRIVATE KEY-----")


def test_manual_job_trigger_creates_pending_job(monkeypatch) -> None:
    class SuccessfulTask:
        @staticmethod
        def delay(_job_run_id: int) -> None:
            return None

    fake_tasks = ModuleType("app.tasks")
    fake_tasks.apply_warp_key = SuccessfulTask()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.tasks", fake_tasks)
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret"))
    headers = auth_headers(client)

    ssh_key = client.post(
        "/ssh-keys",
        headers=headers,
        json={"name": "prod-root", "private_key": "key"},
    ).json()
    host = client.post(
        "/hosts",
        headers=headers,
        json={
            "name": "edge-1",
            "address": "192.0.2.10",
            "ssh_username": "root",
            "ssh_key_id": ssh_key["id"],
        },
    ).json()

    response = client.post(f"/jobs/hosts/{host['id']}/run", headers=headers)

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["trigger"] == "manual"


def test_create_password_host_masks_password() -> None:
    session_factory = make_session_factory("sqlite:///:memory:")
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret", session_factory=session_factory))
    headers = auth_headers(client)

    response = client.post(
        "/hosts",
        headers=headers,
        json={
            "name": "edge-pass",
            "address": "192.0.2.11",
            "ssh_username": "root",
            "auth_mode": "password",
            "ssh_password": "secret",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["auth_mode"] == "password"
    assert body["ssh_key_id"] is None
    assert body["has_password"] is True
    assert "secret" not in response.text
    with session_factory() as session:
        host = session.query(Host).one()
    assert host.auth_mode == HostAuthMode.password
    assert host.encrypted_password != "secret"


def test_create_hosts_validate_auth_credentials() -> None:
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret"))
    headers = auth_headers(client)

    missing_key = client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-key", "address": "192.0.2.12", "ssh_username": "root", "auth_mode": "key"},
    )
    missing_password = client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-pass", "address": "192.0.2.13", "ssh_username": "root", "auth_mode": "password"},
    )

    assert missing_key.status_code == 400
    assert missing_key.json()["detail"] == "ssh key is required for key auth"
    assert missing_password.status_code == 400
    assert missing_password.json()["detail"] == "ssh password is required for password auth"


def test_warp_status_uses_password_auth(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_run_warp_status(_host, _private_data_dir, *, private_key=None, password=None):
        captured["private_key"] = private_key
        captured["password"] = password
        return {"status": "account_status", "account_present": True, "valid": True, "license_tail": "abc1"}

    fake_runner = ModuleType("app.remote.ansible_runner")
    fake_runner.run_warp_status = fake_run_warp_status  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.remote.ansible_runner", fake_runner)
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret"))
    headers = auth_headers(client)
    host = client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-pass", "address": "192.0.2.11", "ssh_username": "root", "auth_mode": "password", "ssh_password": "secret"},
    ).json()

    response = client.post(f"/hosts/{host['id']}/warp-status", headers=headers)

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert captured == {"private_key": None, "password": "secret"}


def test_manual_job_trigger_reports_worker_enqueue_failure(monkeypatch) -> None:
    class BrokenTask:
        @staticmethod
        def delay(_job_run_id: int) -> None:
            raise RuntimeError("redis unavailable")

    fake_tasks = ModuleType("app.tasks")
    fake_tasks.apply_warp_key = BrokenTask()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.tasks", fake_tasks)
    session_factory = make_session_factory("sqlite:///:memory:")
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret", session_factory=session_factory))
    headers = auth_headers(client)

    ssh_key = client.post("/ssh-keys", headers=headers, json={"name": "prod-root", "private_key": "key"}).json()
    host = client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-1", "address": "192.0.2.10", "ssh_username": "root", "ssh_key_id": ssh_key["id"]},
    ).json()

    response = client.post(f"/jobs/hosts/{host['id']}/run", headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "worker queue unavailable"
    with session_factory() as session:
        run = session.query(JobRun).one()
    assert run.status.value == "failed"
    assert run.summary == "failed to enqueue worker task"


def test_full_operator_crud_flow() -> None:
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret"))
    headers = auth_headers(client)

    ssh_key = client.post("/ssh-keys", headers=headers, json={"name": "ops", "private_key": "private"}).json()
    host = client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-prod", "address": "10.0.0.10", "ssh_username": "root", "ssh_key_id": ssh_key["id"]},
    ).json()
    source = client.post(
        "/telegram-sources",
        headers=headers,
        json={
            "name": "owned",
            "access_mode": "bot",
            "channel_ref": "@owned",
            "regex": r"\bKEY-\d+\b",
            "secret": "bot-token",
        },
    ).json()
    warp_key = client.post("/warp-keys", headers=headers, json={"value": "KEY-42", "source_id": source["id"]}).json()
    schedule = client.post(
        "/schedules",
        headers=headers,
        json={"host_id": host["id"], "kind": "interval", "interval_seconds": 3600, "timezone": "Europe/Moscow"},
    ).json()

    assert client.get("/hosts", headers=headers).json()[0]["name"] == "edge-prod"
    assert client.get("/telegram-sources", headers=headers).json()[0]["name"] == "owned"
    assert client.get("/warp-keys", headers=headers).json()[0]["tail"] == "Y-42"
    assert client.get("/schedules", headers=headers).json()[0]["id"] == schedule["id"]

    invalidated = client.post(f"/warp-keys/{warp_key['id']}/invalidate", headers=headers)
    assert invalidated.status_code == 200
    assert invalidated.json()["status"] == "invalid"

    deleted = client.delete(f"/hosts/{host['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/hosts", headers=headers).json() == []


def test_delete_host_removes_dependent_schedules_and_jobs(monkeypatch) -> None:
    class SuccessfulTask:
        @staticmethod
        def delay(_job_run_id: int) -> None:
            return None

    fake_tasks = ModuleType("app.tasks")
    fake_tasks.apply_warp_key = SuccessfulTask()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.tasks", fake_tasks)
    session_factory = make_session_factory("sqlite:///:memory:")
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret", session_factory=session_factory))
    headers = auth_headers(client)

    ssh_key = client.post("/ssh-keys", headers=headers, json={"name": "ops", "private_key": "private"}).json()
    host = client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-prod", "address": "10.0.0.10", "ssh_username": "root", "ssh_key_id": ssh_key["id"]},
    ).json()
    client.post(
        "/schedules",
        headers=headers,
        json={"host_id": host["id"], "kind": "interval", "interval_seconds": 3600, "timezone": "Europe/Moscow"},
    )
    client.post(f"/jobs/hosts/{host['id']}/run", headers=headers)

    response = client.delete(f"/hosts/{host['id']}", headers=headers)

    assert response.status_code == 204
    with session_factory() as session:
        assert session.query(HostSchedule).count() == 0
        assert session.query(JobRun).count() == 0


def test_delete_assigned_ssh_key_returns_conflict() -> None:
    session_factory = make_session_factory("sqlite:///:memory:")
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret", session_factory=session_factory))
    headers = auth_headers(client)

    ssh_key = client.post("/ssh-keys", headers=headers, json={"name": "ops", "private_key": "private"}).json()
    client.post(
        "/hosts",
        headers=headers,
        json={"name": "edge-prod", "address": "10.0.0.10", "ssh_username": "root", "ssh_key_id": ssh_key["id"]},
    )

    response = client.delete(f"/ssh-keys/{ssh_key['id']}", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "ssh key is assigned to one or more hosts"
    with session_factory() as session:
        assert session.query(SshKey).count() == 1


def test_delete_telegram_source_keeps_keys_as_manual() -> None:
    session_factory = make_session_factory("sqlite:///:memory:")
    client = TestClient(create_app(database_url="sqlite:///:memory:", secret_key="test-secret", session_factory=session_factory))
    headers = auth_headers(client)

    source = client.post(
        "/telegram-sources",
        headers=headers,
        json={
            "name": "owned",
            "access_mode": "bot",
            "channel_ref": "@owned",
            "regex": r"\bKEY-\d+\b",
            "secret": "bot-token",
        },
    ).json()
    client.post("/warp-keys", headers=headers, json={"value": "KEY-42", "source_id": source["id"]})

    response = client.delete(f"/telegram-sources/{source['id']}", headers=headers)

    assert response.status_code == 204
    with session_factory() as session:
        assert session.query(TelegramSource).count() == 0
        assert session.query(WarpKey).one().source_id is None


def test_scheduler_sync_adds_and_removes_database_schedules() -> None:
    session_factory = make_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    with session_factory() as session:
        host = Host(name="edge-prod", address="10.0.0.10", ssh_username="root")
        session.add(host)
        session.commit()
        schedule = HostSchedule(host_id=host.id, kind=ScheduleKind.interval, interval_seconds=3600, timezone="Europe/Moscow", enabled=True)
        session.add(schedule)
        session.commit()
        schedule_id = schedule.id

    scheduler = BlockingScheduler(timezone="Europe/Moscow")
    sync_schedules(scheduler, session_factory)

    assert scheduler.get_job(f"schedule-{schedule_id}") is not None

    with session_factory() as session:
        schedule = session.get(HostSchedule, schedule_id)
        assert schedule is not None
        schedule.enabled = False
        session.commit()

    sync_schedules(scheduler, session_factory)

    assert scheduler.get_job(f"schedule-{schedule_id}") is None


def test_worker_restores_previous_account_after_new_keys_are_exhausted(tmp_path, monkeypatch) -> None:
    fake_remote_runner = ModuleType("app.remote.ansible_runner")
    fake_remote_runner.run_warp_playbook = lambda *args, **kwargs: {}  # type: ignore[attr-defined]
    fake_remote_runner.run_warp_restore_latest = lambda *args, **kwargs: {}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.remote.ansible_runner", fake_remote_runner)
    from app import tasks

    database_url = f"sqlite:///{tmp_path / 'worker.db'}"
    monkeypatch.setattr(tasks.settings, "database_url", database_url)
    monkeypatch.setattr(tasks.settings, "secret_key", "test-secret")
    monkeypatch.setattr(tasks.settings, "ansible_private_data_dir", str(tmp_path))
    session_factory = make_session_factory(database_url)
    init_db(session_factory)
    with session_factory() as session:
        host = Host(
            name="edge-pass",
            address="192.0.2.20",
            ssh_username="root",
            auth_mode=HostAuthMode.password,
            encrypted_password=encrypt_secret("test-secret", "ssh-secret"),
        )
        session.add(host)
        session.flush()
        session.add_all(
            [
                WarpKey(encrypted_value=encrypt_secret("test-secret", "KEY-1") or "", fingerprint="sha256:key1", tail="EY-1"),
                WarpKey(encrypted_value=encrypt_secret("test-secret", "KEY-2") or "", fingerprint="sha256:key2", tail="EY-2"),
            ],
        )
        run = JobRun(host_id=host.id, trigger="manual")
        session.add(run)
        session.commit()
        run_id = run.id

    attempted_keys: list[str] = []
    restored: dict[str, str | None] = {}

    def fake_run_warp_playbook(_host, license_key, _private_data_dir, *, private_key=None, password=None):
        attempted_keys.append(license_key)
        assert private_key is None
        assert password == "ssh-secret"
        return {"status": "license_failed", "license_step_reached": True, "reason": "bad key"}

    def fake_run_warp_restore_latest(_host, _private_data_dir, *, private_key=None, password=None):
        restored["private_key"] = private_key
        restored["password"] = password
        return {"status": "account_restored", "valid": True, "reason": "restored"}

    monkeypatch.setattr(tasks, "run_warp_playbook", fake_run_warp_playbook)
    monkeypatch.setattr(tasks, "run_warp_restore_latest", fake_run_warp_restore_latest)

    result = tasks.apply_warp_key(run_id)

    assert result["status"] == "exhausted"
    assert attempted_keys == ["KEY-1", "KEY-2"]
    assert restored == {"private_key": None, "password": "ssh-secret"}
    with session_factory() as session:
        run = session.get(JobRun, run_id)
        assert run is not None
        assert run.summary == "no new WARP keys worked; previous account restored and validated"
        assert session.query(WarpKey).filter_by(status=KeyStatus.invalid).count() == 2
