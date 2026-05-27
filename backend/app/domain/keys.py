import hashlib
import re


DEFAULT_WARP_KEY_REGEX = r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b"


def normalize_regex_pattern(regex: str) -> str:
    return re.sub(r"\\\\([bBdDsSwW])", r"\\\1", regex.strip())


def normalize_warp_key(value: str) -> str:
    return value.strip()


def extract_warp_keys(text: str, regex: str = DEFAULT_WARP_KEY_REGEX) -> list[str]:
    pattern = re.compile(normalize_regex_pattern(regex))
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        value = normalize_warp_key(match.group(0))
        identity = value.casefold()
        if identity not in seen:
            found.append(value)
            seen.add(identity)
    return found


def fingerprint_secret(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    tail = value[-4:] if len(value) >= 4 else value
    return f"sha256:{digest[:24]}:{tail}"
