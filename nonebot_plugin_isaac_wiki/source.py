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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

log = logging.getLogger("isaac_wiki")

API = "https://isaac.huijiwiki.com/api.php"
BASE = "https://isaac.huijiwiki.com"
HOME = f"{BASE}/wiki/%E9%A6%96%E9%A1%B5"

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


def launch_options(headless: bool, proxy: str = "", channel: str = "") -> dict[str, Any]:
    """浏览器启动参数（纯函数，方便单测）。

    ``proxy`` 走 Playwright 的启动代理，页面内的 ``fetch`` 也自动跟着走——
    服务器 IP 被 Cloudflare 挑战时，配一个能过的代理就行。
    ``channel`` 可以指定 ``chrome`` / ``msedge``：装过真浏览器时用它，比 Playwright
    自带的 chromium 更容易过 Cloudflare。
    """
    options: dict[str, Any] = {"headless": headless, "args": launch_args()}
    edge = system_edge()
    if edge:
        options["executable_path"] = edge
    elif channel:
        options["channel"] = channel
    if proxy:
        options["proxy"] = {"server": proxy}
    return options


def playwright_api():
    """挑一个浏览器驱动：装了 patchright 就优先用它。

    patchright 是 playwright 的补丁版，专门抹掉 CDP（自动化）痕迹——无头 Chromium
    被 Cloudflare 挑战时，它是最省事的一条路（不用代理，装个包就行）。
    """
    try:
        from patchright.async_api import async_playwright  # type: ignore

        return async_playwright, "patchright"
    except Exception:  # noqa: BLE001
        from playwright.async_api import async_playwright

        return async_playwright, "playwright"


# 无头 Linux 上最扎眼的机器特征：WebGL 报 SwiftShader（软件渲染）、插件列表空、
# window.chrome 缺字段。抹掉它们比换 IP 便宜得多。放在 init script 里，
# 每个新文档生效前先跑（add_init_script 收的是脚本文本，不是箭头函数）。
STEALTH_JS = """
try {
  const def = (obj, prop, value) => {
    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); }
    catch (e) {}
  };

  // WebGL：把 SwiftShader / Mesa 换成一块真实显卡的名字
  const GPU = { vendor: 'Intel Inc.', renderer: 'ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)' };
  for (const name of ['WebGLRenderingContext', 'WebGL2RenderingContext']) {
    const proto = window[name] && window[name].prototype;
    if (!proto) continue;
    const original = proto.getParameter;
    proto.getParameter = function (param) {
      if (param === 37445) return GPU.vendor;      // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return GPU.renderer;    // UNMASKED_RENDERER_WEBGL
      return original.apply(this, arguments);
    };
  }

  // 插件列表：无头模式是空的
  const plugins = [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  ];
  if (navigator.plugins && navigator.plugins.length === 0) {
    def(navigator, 'plugins', plugins);
    def(navigator, 'mimeTypes', [{ type: 'application/pdf' }, { type: 'text/pdf' }]);
  }

  def(navigator, 'languages', ['zh-CN', 'zh', 'en']);
  if (!navigator.hardwareConcurrency || navigator.hardwareConcurrency < 4) {
    def(navigator, 'hardwareConcurrency', 8);
  }
  if (!navigator.deviceMemory) def(navigator, 'deviceMemory', 8);
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) window.chrome.runtime = {};

  // webdriver 已经被启动参数关掉，这里再兜一层
  def(navigator, 'webdriver', false);
} catch (e) { /* 隐身脚本出错也不能影响页面 */ }
"""


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

    def __init__(self, *, headless: bool = True, timeout: float = 30.0,
                 proxy: str = "", channel: str = "") -> None:
        self.headless = headless
        self.timeout = timeout
        self.proxy = proxy
        self.channel = channel
        self.user_agent = ""
        self.driver = ""
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._ready = False
        self.content_ok = False       # 会话首页是否真的拿到了正文
        self.challenged = False       # 是不是被 Cloudflare 拦在挑战页上了

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        api, self.driver = playwright_api()
        if self.driver != "playwright":
            log.info("isaac_wiki: 使用 %s 驱动浏览器（CDP 痕迹更少）", self.driver)
        self._pw = await api().start()
        self._browser = await self._pw.chromium.launch(
            **launch_options(self.headless, self.proxy, self.channel)
        )
        self.user_agent = await self._clean_ua() or ""
        kwargs: dict[str, Any] = {
            "locale": "zh-CN",
            "viewport": {"width": 1400, "height": 900},
        }
        if self.user_agent:
            kwargs["user_agent"] = self.user_agent
        self._context = await self._browser.new_context(
            **kwargs
        )
        try:
            await self._context.add_init_script(STEALTH_JS)
        except Exception as exc:  # noqa: BLE001
            log.warning("isaac_wiki: 注入隐身脚本失败（%s），继续跑", exc)
        self._page = await self._context.new_page()

    async def _clean_ua(self) -> str | None:
        """浏览器自报的 UA，去掉里面的 ``Headless`` 字样。

        实测（同一台机器、同一个 IP）：用 Playwright 的默认 UA 会被 Cloudflare 挑战，
        去掉 ``HeadlessChrome`` 里的 ``Headless`` 就正常放行——版本号保持浏览器真实
        自报的，不去冒充别的浏览器，免得和 Client Hints 打起来。
        """
        try:
            temp = await self._browser.new_context()
            try:
                page = await temp.new_page()
                ua = await page.evaluate("() => navigator.userAgent")
            finally:
                await temp.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("isaac_wiki: 取 UA 失败（%s），沿用浏览器默认", exc)
            return None
        if not isinstance(ua, str) or not ua:
            return None
        return ua.replace("Headless", "")

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
        state = await self._wait_content()
        self.content_ok = state == "content"
        self.challenged = state == "challenge"
        if not self.content_ok:
            log.warning(
                "isaac_wiki: 会话首页没拿到正文（%s）",
                "停在被 Cloudflare 挑战的页面上" if self.challenged else "页面空白",
            )
            if self.challenged and not self.proxy:
                log.warning("isaac_wiki: 可配 ISAAC_WIKI_PROXY 让浏览器走代理")
        self._ready = True

    async def _page_state(self) -> str:
        """'content' | 'challenge' | 'blank'。"""
        try:
            return await self._page.evaluate(
                """() => {
                     const body = document.body ? document.body.innerText.slice(0, 400) : '';
                     const text = (document.title || '') + ' ' + body;
                     const marks = ['请稍候', 'Just a moment', 'Checking your browser',
                                    'Attention Required', 'cf_chl_opt'];
                     if (marks.some((m) => text.includes(m))
                         || document.querySelector(
                              '#challenge-running, .cf-browser-verification, #cf-chl-wrap')) {
                       return 'challenge';
                     }
                     const r = document.querySelector('.mw-parser-output');
                     return (r && r.innerHTML.length > 2000) ? 'content' : 'blank';
                   }"""
            )
        except Exception:  # noqa: BLE001
            return "blank"

    async def _wait_content(self, *, timeout: float = 25.0,
                            challenge_extra: float = 35.0) -> str:
        """等正文。撞上 Cloudflare 挑战页就多给点时间——它通常几秒后自己放行。"""
        start = time.monotonic()
        deadline = start + timeout
        extended = False
        state = "blank"
        while time.monotonic() < deadline:
            state = await self._page_state()
            if state == "content":
                return state
            if state == "challenge" and not extended:
                deadline = start + timeout + challenge_extra
                extended = True
                log.warning("isaac_wiki: 遇到 Cloudflare 挑战页，多等 %.0f 秒", challenge_extra)
            await asyncio.sleep(2.0)
        return state

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
            if status == 403 and self.challenged:
                raise RuntimeError(
                    "Cloudflare 挑战未通过（服务器出网 IP 被判成机器人）；"
                    "给插件配 ISAAC_WIKI_PROXY 走代理即可"
                )
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
