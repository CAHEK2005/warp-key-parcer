from fastapi.testclient import TestClient
import sys
from types import ModuleType

from app.db import init_db, make_session_factory
from app.main import _create_default_admin, create_app
from app.models import AdminUser, HostSchedule, JobRun, SshKey, TelegramSource, WarpKey
from app.security import verify_password


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
