"""Load .env into os.environ — shared by every eval entry point."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: str | Path | None = None) -> None:
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
