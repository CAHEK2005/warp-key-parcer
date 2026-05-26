from fastapi.testclient import TestClient

from app.main import create_app


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
