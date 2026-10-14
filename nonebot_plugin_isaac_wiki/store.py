"""Tiny JSON cache + tolerant file IO (atomic replace)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FALLBACK_DIR = Path("data") / "isaac_wiki"


def data_dir() -> Path:
    """Plugin data directory.

    Uses nonebot-plugin-localstore when running inside the bot; falls back to
    ``./data/isaac_wiki`` for the offline tools.
    """
    try:
        import nonebot_plugin_localstore as store  # type: ignore

        return Path(store.get_plugin_data_dir())
    except Exception:  # noqa: BLE001
        return FALLBACK_DIR


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def cache_get(key: str, ttl: float) -> Any | None:
    path = cache_dir() / f"{key}.json"
    payload = read_json(path)
    if not payload:
        return None
    if ttl > 0 and time.time() - payload.get("_ts", 0) > ttl:
        return None
    return payload.get("value")


def cache_set(key: str, value: Any) -> None:
    write_json(cache_dir() / f"{key}.json", {"_ts": time.time(), "value": value})
