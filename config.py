import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"

def read_env() -> dict[str, str]:
    values = {}
    if not ENV_FILE.exists():
        return values
    if ENV_FILE.is_symlink():
        raise RuntimeError(".env cannot be a symlink")
    if os.name == "posix" and ENV_FILE.stat().st_mode & 0o077:
        raise RuntimeError("Run chmod 600 .env")

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError("Invalid .env line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise RuntimeError("Invalid environment key")
        values[key] = value.strip().strip('"').strip("'")
    return values
