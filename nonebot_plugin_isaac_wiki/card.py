"""Turn a wiki page into a card image ("方案 A": 净化克隆 + 截图).

The rendered wiki HTML is already styled and self-contained, so the card is
produced by clipping the article area of the live page, with a stylesheet
injected that:

* drops navigation / footer / login chrome;
* turns the float-based desktop layout into a single centered column;
* keeps the infobox (and the item grids, pool tables, Anm2 animations) exactly
  as the wiki renders them.
"""

from __future__ import annotations

from pathlib import Path

CONTENT_SELECTOR = "#mw-content-text"

CARD_CSS = """
:root { color-scheme: light; }
html, body { background: #f7f7f9 !important; }
body { margin: 0 !important; padding: 0 !important; }

/* hide site chrome */
.header-container, #header, .navbar, .nav, .site-nav, header, footer,
#footer, .footer, .mw-editsection, .mw-jump-link, .printfooter,
.mw-indicators, #catlinks, .catlinks, .lang-links, .huiji-footer,
.navbar-fixed-top, .notification-area, .mw-notification-area,
.mw-notification-area-overlay, .mobile-only, .noprint,
.bg-canvas-container, .anm2-tab-group-select-panel-container {
  display: none !important;
}

/* single centered column */
#content, #content-container, .mw-body, #wiki-outer-body, #wrapper, .row,
#mw-content-text, .mw-body-content {
  width: auto !important;
  max-width: none !important;
  margin: 0 !important;
  padding: 0 !important;
  display: block !important;
  float: none !important;
  position: static !important;
}

#mw-content-text, .mw-body-content { padding: 10px 12px 18px 12px !important; }
.mw-parser-output { width: auto !important; max-width: none !important; }

/* infobox becomes a centered block on top */
.infobox {
  float: none !important;
  clear: both !important;
  position: static !important;
  margin: 8px auto 16px auto !important;
  display: table !important;
}

/* keep media inside the card */
.mw-parser-output img,
.mw-parser-output table,
.mw-parser-output .anm2player,
.mw-parser-output div[style*="width"] {
  max-width: 100% !important;
}
.mw-parser-output table { height: auto !important; }

/* tighter typography for chat reading */
.mw-parser-output p, .mw-parser-output li { line-height: 1.55 !important; }
.mw-parser-output h1, .mw-parser-output h2, .mw-parser-output h3 {
  line-height: 1.35 !important;
  clear: both !important;
}
"""

NOT_FOUND_MARKERS = ("NOT FOUND", "您正在浏览的页面不存在")


async def render_card(
    browser_wiki,
    title: str,
    out_path: str | Path,
    *,
    width: int = 760,
    full_page: bool = True,
    scale: int = 2,
) -> Path:
    """Navigate to the page and screenshot the article area as a card."""
    page = browser_wiki.page
    await browser_wiki.ensure_session()

    url = await browser_wiki.page_url(title)
    await page.set_viewport_size({"width": width, "height": 800})

    # 这个 wiki 的 DOMContentLoaded / load 事件很晚（甚至不触发），但正文
    # 在服务端 HTML 里已就绪。所以只用短超时等待导航「已提交」，随后靠
    # 选择器判断正文是否可用即可，不要等 load 事件。
    try:
        await page.goto(url, wait_until="commit", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.wait_for_selector(".mw-parser-output", timeout=25000)
    except Exception:  # noqa: BLE001
        pass

    await page.add_style_tag(content=CARD_CSS)
    await page.wait_for_timeout(600)  # 让重排/懒加载图片稳定

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        locator = page.locator(CONTENT_SELECTOR)
        if await locator.count() > 0:
            await locator.first.screenshot(path=str(out), timeout=60000)
            return out
    except Exception:  # noqa: BLE001
        pass
    await page.screenshot(path=str(out), full_page=full_page)
    return out


async def is_not_found(browser_wiki, title: str) -> bool:
    page = browser_wiki.page
    await browser_wiki.ensure_session()
    url = await browser_wiki.page_url(title)
    await page.goto(url, wait_until="domcontentloaded", timeout=browser_wiki.timeout * 1000)
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    return any(marker in text for marker in NOT_FOUND_MARKERS)
