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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

API = "https://isaac.huijiwiki.com/api.php"
BASE = "https://isaac.huijiwiki.com"
HOME = f"{BASE}/wiki/%E9%A6%96%E9%A1%B5"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)


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

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        # Windows local development: reuse the installed Edge instead of
        # downloading a Playwright browser.
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if os.name == "nt" and Path(edge).exists():
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
        except Exception:  # noqa: BLE001
            pass
        self._ready = True

    async def refresh_session(self) -> None:
        self._ready = False
        await self.ensure_session()

    # -- API ---------------------------------------------------------------
    async def api(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call the MediaWiki API from inside the wiki page."""
        await self.ensure_session()
        query = urlencode({**params, "format": "json"})
        url = f"{API}?{query}"
        async with self._lock:
            result = await self._page.evaluate(
                """async (u) => {
                     const r = await fetch(u, {headers: {'Accept': 'application/json'}});
                     return {status: r.status, body: await r.text()};
                   }""",
                url,
            )
        if result["status"] == 403:
            # session expired: reload once and retry
            await self.refresh_session()
            async with self._lock:
                result = await self._page.evaluate(
                    """async (u) => {
                         const r = await fetch(u, {headers: {'Accept': 'application/json'}});
                         return {status: r.status, body: await r.text()};
                       }""",
                    url,
                )
        if result["status"] != 200:
            raise RuntimeError(f"wiki api status {result['status']}: {result['body'][:200]}")
        import json

        return json.loads(result["body"])

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
