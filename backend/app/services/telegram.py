from datetime import datetime, timezone
import json

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import TelegramClient
from telethon.sessions import StringSession

from app.domain.keys import extract_warp_keys, fingerprint_secret
from app.models import TelegramAccessMode, TelegramSource, WarpKey
from app.security import encrypt_secret


def ingest_messages(session: Session, source: TelegramSource, messages: list[str], secret_key: str) -> int:
    created = 0
    for message in messages:
        for value in extract_warp_keys(message, source.regex):
            fingerprint = fingerprint_secret(value)
            if session.scalar(select(WarpKey).where(WarpKey.fingerprint == fingerprint)) is not None:
                continue
            session.add(
                WarpKey(
                    encrypted_value=encrypt_secret(secret_key, value) or "",
                    fingerprint=fingerprint,
                    tail=value[-4:],
                    source_id=source.id,
                ),
            )
            created += 1
    source.last_sync_at = datetime.now(timezone.utc)
    session.commit()
    return created


async def fetch_telegram_messages(source: TelegramSource, decrypted_secret: str | None) -> list[str]:
    if not decrypted_secret:
        return []
    if source.access_mode == TelegramAccessMode.bot:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"https://api.telegram.org/bot{decrypted_secret}/getUpdates")
            response.raise_for_status()
            payload = response.json()
        messages: list[str] = []
        channel_ref = source.channel_ref.lstrip("@").casefold()
        for update in payload.get("result", []):
            post = update.get("channel_post") or update.get("message") or {}
            chat = post.get("chat") or {}
            username = str(chat.get("username") or "").casefold()
            title = str(chat.get("title") or "").casefold()
            chat_id = str(chat.get("id") or "")
            if channel_ref in {username, title, chat_id}:
                text = post.get("text") or post.get("caption")
                if text:
                    messages.append(text)
        return messages

    credentials = json.loads(decrypted_secret)
    session_string = credentials.get("session_string", "")
    api_id = int(credentials["api_id"])
    api_hash = credentials["api_hash"]
    async with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
        messages = []
        async for message in client.iter_messages(source.channel_ref, limit=int(credentials.get("limit", 100))):
            if message.message:
                messages.append(message.message)
        return messages
