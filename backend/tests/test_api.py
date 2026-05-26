from fastapi.testclient import TestClient

from app.db import init_db, make_session_factory
from app.main import _create_default_admin, create_app
from app.models import AdminUser
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


def test_manual_job_trigger_creates_pending_job() -> None:
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
