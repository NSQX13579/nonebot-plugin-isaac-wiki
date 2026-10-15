"""Browser-backed wiki session.

Everything talks to the wiki through one long-lived headless Chromium:

* the MediaWiki API is called with ``fetch()`` *inside* a wiki page, so the
  request carries the browser's Cloudflare session;
* card screenshots are taken by navigating that same browser to the page.

A plain HTTP client cannot be used: copying the browser cookies does not work
because Cloudflare binds the ``__cf_bm`` cookie to the client's TLS
fingerprint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

log = logging.getLogger("isaac_wiki")

API = "https://isaac.huijiwiki.com/api.php"
BASE = "https://isaac.huijiwiki.com"
HOME = f"{BASE}/wiki/%E9%A6%96%E9%A1%B5"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)

# Windows 本地开发时复用系统 Edge，省得再下 Playwright 浏览器
EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def system_edge() -> str | None:
    if os.name != "nt":
        return None
    for path in EDGE_PATHS:
        if Path(path).exists():
            return path
    return None


def launch_args() -> list[str]:
    """启动参数。

    服务器常以 root 跑，而 Chromium **拒绝在 root 下启动**（除非关掉 sandbox）——
    报错是「Running as root without --no-sandbox is not supported」，而且看起来
    像是「每个条目都失败」。容器里 /dev/shm 往往很小，一并绕开。
    """
    args = ["--disable-blink-features=AutomationControlled"]
    if os.name == "posix":
        args.append("--disable-dev-shm-usage")
        geteuid = getattr(os, "geteuid", None)
        if callable(geteuid) and geteuid() == 0:
            args.append("--no-sandbox")
    return args


@dataclass
class SearchHit:
    title: str
    pageid: int
    snippet: str = ""
    size: int = 0
    wordcount: int = 0


@dataclass
class PageData:
    title: str
    requested: str
    redirect_from: list[str] = field(default_factory=list)
    html: str = ""
    wikitext: str = ""
    categories: list[str] = field(default_factory=list)


class BrowserWiki:
    """A wiki session kept alive by a headless Chromium."""

    def __init__(self, *, headless: bool = True, timeout: float = 30.0) -> None:
        self.headless = headless
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._ready = False
        self.content_ok = False       # 会话首页是否真的拿到了正文

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": launch_args(),
        }
        edge = system_edge()
        if edge:
            kwargs["executable_path"] = edge
        self._browser = await self._pw.chromium.launch(**kwargs)
        self._context = await self._browser.new_context(
            locale="zh-CN",
            user_agent=UA,
            viewport={"width": 1400, "height": 900},
        )
        self._page = await self._context.new_page()

    async def close(self) -> None:
        for obj in (self._browser, self._pw):
            try:
                if obj is self._browser and obj is not None:
                    await obj.close()
                elif obj is self._pw and obj is not None:
                    await obj.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._browser = self._context = self._page = None
        self._ready = False

    @property
    def page(self):
        return self._page

    async def ensure_session(self) -> None:
        """Make sure the browser holds a live Cloudflare session."""
        if self._page is None:
            raise RuntimeError("BrowserWiki.start() has not been called")
        if self._ready:
            return
        # 页面 load 事件很晚甚至不触发，等「已提交」+ 正文选择器即可
        try:
            await self._page.goto(HOME, wait_until="commit", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._page.wait_for_selector(".mw-parser-output", timeout=25000)
            self.content_ok = True
        except Exception:  # noqa: BLE001
            # 没等到正文：多半是被 Cloudflare 挑战了（机房 IP 常见），
            # 记下来，后面 API 报错时能对上号
            self.content_ok = False
            log.warning("isaac_wiki: 首页没等到正文选择器，可能被 Cloudflare 挑战")
        self._ready = True

    async def refresh_session(self) -> None:
        self._ready = False
        await self.ensure_session()

    # -- API ---------------------------------------------------------------
    async def _fetch_in_page(self, url: str) -> tuple[int, str]:
        """在页面里用 fetch 调接口（带着浏览器会话）。"""
        async with self._lock:
            result = await self._page.evaluate(
                """async (u) => {
                     const r = await fetch(u, {headers: {'Accept': 'application/json'}});
                     return {status: r.status, body: await r.text()};
                   }""",
                url,
            )
        return int(result["status"]), str(result["body"])

    async def _navigate_api(self, url: str) -> tuple[int, str]:
        """改成顶层导航取接口内容。

        页内 fetch 被 Cloudflare 挡掉时（403），文档级请求往往还能过；顺便把真实
        状态码带回来，报错才有信息量。
        """
        try:
            resp = await self._page.goto(url, wait_until="commit", timeout=15000)
        except Exception as exc:  # noqa: BLE001
            return 0, f"navigate failed: {type(exc).__name__}: {exc}"
        status = resp.status if resp is not None else 0
        try:
            body = await self._page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
        except Exception:  # noqa: BLE001
            body = ""
        return status, body

    async def api(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call the MediaWiki API from inside the wiki page."""
        await self.ensure_session()
        url = f"{API}?{urlencode({**params, 'format': 'json'})}"
        status, body = await self._fetch_in_page(url)
        if status == 403:
            log.warning("isaac_wiki: api 403，刷新会话后重试")
            await self.refresh_session()
            status, body = await self._fetch_in_page(url)
        if status == 403:
            log.warning("isaac_wiki: api 仍被 403，改用顶层导航")
            status, body = await self._navigate_api(url)
        if status != 200:
            raise RuntimeError(f"wiki api status {status}: {body[:160]!r}")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise RuntimeError(f"wiki api 返回的不是 JSON：{body[:120]!r}") from exc

    async def resolve(self, title: str) -> tuple[str, list[str]]:
        """Follow redirects for *title*; returns (canonical title, redirect chain)."""
        data = await self.api(
            {
                "action": "query",
                "titles": title,
                "redirects": "1",
                "prop": "info",
            }
        )
        query = data.get("query", {})
        redirects = query.get("redirects", [])
        chain = [r["from"] for r in redirects]
        pages = query.get("pages", {})
        normalized = query.get("normalized", [])
        if pages:
            page = next(iter(pages.values()))
            if "missing" in page:
                return "", chain
            canonical = page.get("title") or title
        elif normalized:
            canonical = normalized[0]["to"]
        else:
            canonical = title
        return canonical, chain

    async def search(self, term: str, limit: int = 10) -> list[SearchHit]:
        data = await self.api(
            {
                "action": "query",
                "list": "search",
                "srsearch": term,
                "srlimit": limit,
            }
        )
        hits = []
        for item in data.get("query", {}).get("search", []):
            hits.append(
                SearchHit(
                    title=item.get("title", ""),
                    pageid=item.get("pageid", 0),
                    snippet=item.get("snippet", ""),
                    size=item.get("size", 0),
                    wordcount=item.get("wordcount", 0),
                )
            )
        return hits

    async def fetch(self, title: str) -> PageData:
        """Fetch rendered HTML + wikitext for a page (redirects followed)."""
        parse = await self.api(
            {
                "action": "parse",
                "page": title,
                "prop": "text|wikitext|categories",
                "redirects": "1",
            }
        )
        node = parse.get("parse")
        if node is None:
            raise LookupError(f"page not found: {title}")
        html = node.get("text", {}).get("*", "")
        wikitext = node.get("wikitext", {}).get("*", "")
        cats = [c.get("*", "") for c in node.get("categories", [])]
        return PageData(
            title=node.get("title", title),
            requested=title,
            redirect_from=[r["from"] for r in parse.get("redirects", [])],
            html=html,
            wikitext=wikitext,
            categories=cats,
        )

    async def page_url(self, title: str) -> str:
        return f"{BASE}/wiki/{quote(title.replace(' ', '_'))}"
