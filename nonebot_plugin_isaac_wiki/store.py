"""落盘：卡片缓存（按大小上限 LRU + TTL）与群开关。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FALLBACK_DIR = Path("data") / "isaac_wiki"


def data_dir() -> Path:
    """插件数据目录（机器人里走 localstore，工具里退回 ./data/isaac_wiki）。"""
    try:
        import nonebot_plugin_localstore as localstore  # type: ignore

        return Path(localstore.get_plugin_data_dir())
    except Exception:  # noqa: BLE001
        return FALLBACK_DIR


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def groups_file() -> Path:
    return data_dir() / "groups.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# --- 群开关 -----------------------------------------------------------------
def is_group_enabled(group_id: int) -> bool:
    """默认开启；超管用「以撒开关」关掉后置为 False。"""
    data = _read_json(groups_file(), {})
    return bool(data.get(str(group_id), True))


def set_group_enabled(group_id: int, enabled: bool) -> None:
    data = _read_json(groups_file(), {})
    data[str(group_id)] = bool(enabled)
    _write_json(groups_file(), data)


def toggle_group(group_id: int) -> bool:
    now = is_group_enabled(group_id)
    set_group_enabled(group_id, not now)
    return not now


# --- 卡片缓存 ---------------------------------------------------------------
def cache_key(title: str, revid: int, policy_version: str, width: int) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
    return f"{safe}__r{revid}__{policy_version}__w{width}"


def meta_path(key: str) -> Path:
    """卡片对应的元数据（裁剪计划 + 分节）。

    没有它，缓存命中时只能拿回一张 PNG，卡片下的数字菜单就没了——
    第二次查同一条目的人会「看到图但选不了」。
    """
    return cache_dir() / f"{key}.json"


def get_meta(key: str) -> dict | None:
    data = _read_json(meta_path(key), None)
    return data if isinstance(data, dict) else None


def put_meta(key: str, payload: dict) -> Path:
    path = meta_path(key)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _drop(path: Path) -> int:
    """删掉卡片及其元数据，返回实际删除的文件数。"""
    removed = 0
    for p in (path, meta_path(path.stem)):
        try:
            if p.is_file():
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def get_cached(key: str, *, ttl_days: int = 7) -> Path | None:
    path = cache_dir() / f"{key}.png"
    if not path.is_file():
        return None
    if ttl_days > 0 and time.time() - path.stat().st_mtime > ttl_days * 86400:
        _drop(path)
        return None
    # 更新访问时间，供 LRU 使用
    try:
        os.utime(path, None)
    except OSError:
        pass
    return path


def put_cached(key: str, src: Path, *, limit_mb: int = 500, ttl_days: int = 7) -> Path:
    """写入缓存并顺手清理（超过总量上限就淘汰最久未用的）。"""
    dst = cache_dir() / f"{key}.png"
    tmp = dst.with_suffix(".png.tmp")
    tmp.write_bytes(Path(src).read_bytes())
    os.replace(tmp, dst)
    prune_cache(limit_mb=limit_mb, ttl_days=ttl_days)
    return dst


def prune_cache(*, limit_mb: int = 500, ttl_days: int = 7) -> int:
    """删过期文件；仍超上限就按最近访问时间淘汰最旧的。返回删除的文件数。

    每张卡片会连带删掉配套的 ``.json`` 元数据；最后再扫一遍孤儿元数据，
    避免「PNG 没了但菜单还在」。
    """
    files = [p for p in cache_dir().glob("*.png") if p.is_file()]
    removed = 0
    if ttl_days > 0:
        deadline = time.time() - ttl_days * 86400
        for p in files:
            try:
                if p.stat().st_mtime < deadline:
                    removed += _drop(p)
            except OSError:
                continue
        files = [p for p in cache_dir().glob("*.png") if p.is_file()]

    limit = limit_mb * 1024 * 1024
    total = sum(p.stat().st_size for p in files if p.is_file())
    if total > limit:
        for p in sorted(files, key=lambda x: x.stat().st_mtime):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            removed += _drop(p)
            total -= size
            if total <= limit:
                break

    alive = {p.stem for p in cache_dir().glob("*.png")}
    for meta in cache_dir().glob("*.json"):
        if meta.stem in alive:
            continue
        try:
            meta.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def cache_stats() -> dict:
    files = [p for p in cache_dir().glob("*.png") if p.is_file()]
    return {"count": len(files), "bytes": sum(p.stat().st_size for p in files)}
