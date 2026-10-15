"""渲染引擎：常驻浏览器、串行队列、看门狗、缓存读写。"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from . import card, parse, store
from .card import CardResult
from .source import BrowserWiki

log = logging.getLogger("isaac_wiki")

POLICY_VERSION = "p1"      # 策略改了要 +1，缓存自动失效

_wiki: BrowserWiki | None = None
_lock = asyncio.Lock()
_waiting = 0
_consecutive_failures = 0
_last_used = 0.0


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


def idle_seconds() -> float:
    return time.time() - _last_used if _last_used else 0.0


def busy_count() -> int:
    return _waiting


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
    use_cache: bool = True,
) -> CardResult:
    """渲染一张卡片（带缓存、串行、失败重试一次）。"""
    global _waiting, _consecutive_failures, _last_used

    if _waiting >= queue_limit:
        raise QueueFull("渲染队列已满")
    _waiting += 1
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
                        return CardResult(path=hit, height=0, plan=_empty_plan())
                except Exception:  # noqa: BLE001
                    key = None

            out = store.cache_dir() / f"_tmp_{int(time.time() * 1000)}.png"
            last_err: Exception | None = None
            for attempt in (1, 2):
                wiki = await _get_wiki(headless, timeout)
                try:
                    res = await card.render_card(
                        wiki, title, out, expect_heading=None, width=width,
                        is_boss=is_boss, keep_idx=keep_idx,
                    )
                    _consecutive_failures = 0
                    _last_used = time.time()
                    if key:
                        store.put_cached(key, res.path, limit_mb=cache_mb,
                                         ttl_days=cache_ttl_days)
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


def _empty_plan():
    from .policy import Plan

    return Plan()


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
