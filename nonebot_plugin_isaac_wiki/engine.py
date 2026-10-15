"""渲染引擎：常驻浏览器、串行队列、看门狗、缓存读写。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict as _asdict, fields as _fields
from pathlib import Path

from . import card, parse, store
from .card import CardResult
from .policy import Plan, Section
from .source import BrowserWiki

log = logging.getLogger("isaac_wiki")

POLICY_VERSION = "p3"      # 策略/缓存格式/卡片外观改了要 +1，缓存自动失效

_wiki: BrowserWiki | None = None
_lock = asyncio.Lock()
_waiting = 0
_consecutive_failures = 0
_last_used = 0.0
_watchdog: asyncio.Task | None = None
_WATCHDOG_MAX_INTERVAL = 120.0


async def _get_wiki(headless: bool, timeout: float) -> BrowserWiki:
    global _wiki
    if _wiki is None:
        _wiki = BrowserWiki(headless=headless, timeout=timeout)
        await _wiki.start()
    return _wiki


async def close_wiki() -> None:
    global _wiki
    if _wiki is not None:
        try:
            await _wiki.close()
        except Exception:  # noqa: BLE001
            pass
        _wiki = None


async def restart_wiki() -> None:
    await close_wiki()


async def shutdown() -> None:
    """进程退出钩子：停看门狗并关掉常驻浏览器，别留孤儿 Chromium。"""
    global _watchdog
    task, _watchdog = _watchdog, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    await close_wiki()


def idle_seconds() -> float:
    return time.time() - _last_used if _last_used else 0.0


def busy_count() -> int:
    return _waiting


def _idle_expired(idle_minutes: float) -> bool:
    """该不该因为空闲把浏览器关掉（纯判断，方便单测）。"""
    return (
        idle_minutes > 0
        and _wiki is not None
        and _waiting == 0
        and not _lock.locked()
        and idle_seconds() > idle_minutes * 60
    )


async def _idle_loop(idle_minutes: float) -> None:
    interval = max(30.0, min(_WATCHDOG_MAX_INTERVAL, idle_minutes * 60 / 4))
    while True:
        await asyncio.sleep(interval)
        try:
            if _idle_expired(idle_minutes):
                log.info("isaac_wiki: 浏览器空闲超过 %s 分钟，关闭", idle_minutes)
                await close_wiki()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("isaac_wiki: 空闲看门狗出错 %s", exc)


def start_watchdog(idle_minutes: float) -> None:
    """按需启动空闲看门狗（只启一个）。``idle_minutes <= 0`` 表示不自动关。"""
    global _watchdog
    if idle_minutes <= 0:
        return
    if _watchdog is not None and not _watchdog.done():
        return
    _watchdog = asyncio.create_task(_idle_loop(idle_minutes))


def _plan_from_dict(data: object) -> Plan:
    if not isinstance(data, dict):
        return Plan()
    names = {f.name for f in _fields(Plan)}
    try:
        return Plan(**{k: v for k, v in data.items() if k in names})
    except Exception:  # noqa: BLE001
        return Plan()


def _cached_result(key: str, path: Path) -> CardResult:
    """缓存命中：把元数据一起读回来，菜单/分节跟首次渲染一致。"""
    meta = store.get_meta(key) or {}
    sections: list[Section] = []
    for item in meta.get("sections") or []:
        if not isinstance(item, dict):
            continue
        try:
            sections.append(Section(**item))
        except TypeError:
            continue
    try:
        height = int(meta.get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    return CardResult(
        path=path,
        height=height,
        plan=_plan_from_dict(meta.get("plan")),
        sections=sections,
        page_title=str(meta.get("page_title") or ""),
        en_title=str(meta.get("en_title") or ""),
    )


def _put_meta(key: str, res: CardResult) -> None:
    try:
        store.put_meta(key, {
            "height": res.height,
            "page_title": res.page_title,
            "en_title": res.en_title,
            "plan": _asdict(res.plan),
            "sections": [_asdict(s) for s in res.sections],
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("isaac_wiki: 写缓存元数据失败 %s", exc)


class QueueFull(RuntimeError):
    pass


async def render_entry(
    title: str,
    *,
    headless: bool = True,
    timeout: float = 30.0,
    width: int = 760,
    is_boss: bool = False,
    keep_idx: list[int] | None = None,
    queue_limit: int = 3,
    cache_mb: int = 500,
    cache_ttl_days: int = 7,
    idle_close_minutes: int = 10,
    use_cache: bool = True,
) -> CardResult:
    """渲染一张卡片（带缓存、串行、失败重试一次）。"""
    global _waiting, _consecutive_failures, _last_used

    if _waiting >= queue_limit:
        raise QueueFull("渲染队列已满")
    _waiting += 1
    start_watchdog(idle_close_minutes)
    try:
        async with _lock:
            key = None
            if use_cache:
                try:
                    revid = await _page_revid(title, headless, timeout)
                    key = store.cache_key(title, revid, POLICY_VERSION, width)
                    hit = store.get_cached(key, ttl_days=cache_ttl_days)
                    if hit is not None:
                        _last_used = time.time()
                        log.debug("isaac_wiki: 命中缓存 %s", key)
                        return _cached_result(key, hit)
                except Exception:  # noqa: BLE001
                    key = None

            out = store.cache_dir() / f"_tmp_{int(time.time() * 1000)}.png"
            last_err: Exception | None = None
            for attempt in (1, 2):
                wiki = await _get_wiki(headless, timeout)
                try:
                    res = await card.render_card(
                        wiki, title, out, page_title=title, width=width,
                        is_boss=is_boss, keep_idx=keep_idx,
                    )
                    _consecutive_failures = 0
                    _last_used = time.time()
                    if key:
                        store.put_cached(key, res.path, limit_mb=cache_mb,
                                         ttl_days=cache_ttl_days)
                        _put_meta(key, res)
                    return res
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    log.warning("isaac_wiki: 渲染失败（第 %s 次）：%s", attempt, exc)
                    _consecutive_failures += 1
                    if _consecutive_failures >= 2:
                        log.warning("isaac_wiki: 连续失败，重启浏览器")
                        await restart_wiki()
                        _consecutive_failures = 0
            raise RuntimeError(f"渲染失败：{last_err}")
    finally:
        _waiting -= 1


async def _page_revid(title: str, headless: bool, timeout: float) -> int:
    wiki = await _get_wiki(headless, timeout)
    data = await wiki.api({
        "action": "query", "titles": title, "prop": "revisions", "rvprop": "ids",
        "redirects": "1",
    })
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        revs = page.get("revisions") or []
        if revs:
            return int(revs[0].get("revid", 0))
    return 0
