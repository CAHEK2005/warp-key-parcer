import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(secret_key: str, value: str | None) -> str | None:
    if value is None:
        return None
    return Fernet(_fernet_key(secret_key)).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(secret_key: str, value: str | None) -> str | None:
    if value is None:
        return None
    return Fernet(_fernet_key(secret_key)).decrypt(value.encode("utf-8")).decode("utf-8")


def create_access_token(secret_key: str, subject: str, minutes: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expires}
    return jwt.encode(payload, secret_key, algorithm="HS256")


def decode_access_token(secret_key: str, token: str) -> str:
    payload = jwt.decode(token, secret_key, algorithms=["HS256"])
    return str(payload["sub"])
