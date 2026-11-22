from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretBox:
    """Symmetric Fernet encryption for API keys & platform credentials at rest."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or self._load_or_create_key()
        self._fernet = Fernet(self._key)

    @staticmethod
    def _load_or_create_key() -> bytes:
        env_key = os.getenv("AGAN_SECRET_KEY", "").strip()
        if env_key:
            return env_key.encode("utf-8")

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        key_file: Path = settings.data_dir / ".secret.key"
        if key_file.exists():
            return key_file.read_bytes().strip()

        new_key = Fernet.generate_key()
        key_file.write_bytes(new_key)
        return new_key

    def encrypt(self, plaintext: str | None) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str | None) -> str:
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return ""


def mask_secret(secret: str | None) -> str:
    """Return a display-safe masked secret string (e.g., sk-****abcd)."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:3]}****{secret[-4:]}"


secret_box = SecretBox()
