"""把 wiki 页面渲染成卡片图（方案 A：净化克隆 + 截图）。

页面本身就带样式，所以卡片的做法是：在真实浏览器里打开页面 → 注入卡片样式
（单列、隐藏站点外壳、标题栏）→ 按 :mod:`policy` 的决策隐藏子树 → 整页滚动
触发贴图绘制 → 截 ``#mw-content-text``。

踩过的坑都固化在这里：

* 这个 wiki 的 ``DOMContentLoaded``/``load`` 很晚甚至不触发，导航只用
  ``wait_until="commit"``，然后等内容选择器；
* 页面会间歇返回「未渲染的空壳」（只有导语、0 个分节），必须检测并重载；
* 正文由 JS 渲染，**同一页面只导航一次**，重复导航会打断渲染；
* 信息框贴图是 Anm2 部件画在 ``<canvas>`` 上的，**只在滚进视口时才绘制**，
  所以截图前必须整页滚一遍；
* 隐藏一律用 ``.isaac-hide``（``display:none !important``），内联 ``display:none``
  会被卡片样式里的 ``display: table !important`` 压过；
* 宽表格被包在横向滚动容器里，会撑到超出卡片；要按卡片宽度写死像素宽 +
  ``table-layout: fixed`` + 允许单元格换行，让它变高变窄而不是被裁字。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .policy import Plan, Section, plan_card

log = logging.getLogger("isaac_wiki")

CONTENT_SELECTOR = "#mw-content-text"

CARD_CSS = """
:root { color-scheme: light; }
html, body { background: #f7f7f9 !important; }
body { margin: 0 !important; padding: 0 !important; }

/* 站点外壳 */
.header-container, #header, .navbar, .nav, .site-nav, header, footer,
#footer, .footer, .mw-editsection, .mw-jump-link, .printfooter,
.mw-indicators, #catlinks, .catlinks, .lang-links, .huiji-footer,
.navbar-fixed-top, .notification-area, .mw-notification-area,
.mw-notification-area-overlay, .mobile-only, .noprint,
.bg-canvas-container, .anm2-tab-group-select-panel-container {
  display: none !important;
}

/* 单列 */
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

/* 信息框居中 */
.infobox {
  float: none !important;
  clear: both !important;
  position: static !important;
  margin: 8px auto 16px auto !important;
  display: table !important;
}

/* 媒体不溢出 */
.mw-parser-output img,
.mw-parser-output table,
.mw-parser-output .anm2player,
.mw-parser-output div[style*="width"] { max-width: 100% !important; }
.mw-parser-output table { height: auto !important; }

/* 手机阅读的字距 */
.mw-parser-output p, .mw-parser-output li { line-height: 1.55 !important; }
.mw-parser-output h1, .mw-parser-output h2, .mw-parser-output h3 {
  line-height: 1.35 !important;
  clear: both !important;
}
"""

HIDE_CLASS_CSS = ".isaac-hide { display: none !important; }"

# 卡片标题栏：页面标题 + 英文名（英文名取信息框的 .infobox-title / <big>）
TITLEBAR_JS = """
() => {
  const h1 = document.querySelector('h1');
  const title = h1 ? h1.innerText.trim() : '';
  if (!title) return '';
  let en = '';
  const box = document.querySelector('.infobox');
  if (box) {
    const head = box.querySelector('.infobox-title')
      || box.querySelector('.infobox-header:has(big)')
      || box.querySelector('.infobox-header');
    if (head) {
      const big = head.querySelector('big');
      if (big) {
        en = big.innerText.trim();
      } else {
        const parts = head.innerText.split('\\n').map((s) => s.trim()).filter(Boolean);
        if (parts.length > 1 && /[A-Za-z]/.test(parts[1])) en = parts[1];
      }
    }
  }
  const root = document.querySelector('#mw-content-text');
  const bar = document.createElement('div');
  bar.className = 'isaac-titlebar';
  bar.style.cssText = 'background:#153970;color:#fff;text-align:center;'
    + 'padding:12px 16px;margin:0 0 10px 0;border-radius:10px;';
  bar.innerHTML = '<div style="font-size:22px;font-weight:bold;letter-spacing:2px">'
    + title + '</div>'
    + (en ? '<div style="font-size:14px;opacity:.75;margin-top:2px">' + en + '</div>' : '');
  root.insertBefore(bar, root.firstChild);
  return title;
}
"""

# 分节树 + 每节净高度（隐藏全部分节时的高度即基线）
SECTIONS_JS = """
() => {
  const root = document.querySelector('.mw-parser-output');
  if (!root) return null;
  const heads = Array.from(root.querySelectorAll('h2,h3,h4,h5,h6'));
  const lvl = (el) => Number(el.tagName[1]);
  const norm = (h) => h.innerText.replace(/\\[\\s*编辑\\s*\\]/g,'').trim();
  const subs = heads.map((h) => {
    const l = lvl(h);
    const els = [h];
    const nextHead = heads[heads.indexOf(h) + 1];
    let n = h.nextElementSibling;
    while (n) {
      if (n.matches && n.matches('h2,h3,h4,h5,h6') && lvl(n) <= l) break;
      if (nextHead && n.contains && n.contains(nextHead)) break;
      els.push(n);
      n = n.nextElementSibling;
    }
    const p = els.find((e) => e.tagName === 'P');
    return {head: h, els, title: norm(h), level: l,
            para: p ? p.innerText.trim().slice(0, 60) : ''};
  });
  const all = [];
  for (const s of subs) for (const e of s.els) if (!all.includes(e)) all.push(e);
  const height = () => Math.round(
    document.querySelector('#mw-content-text').getBoundingClientRect().height);
  const saved = new Map(all.map((e) => [e, e.classList.contains('isaac-hide')]));
  for (const e of all) e.classList.add('isaac-hide');
  const base = height();
  const hs = subs.map((s) => {
    for (const o of subs) {
      if (o === s) continue;
      for (const e of o.els) if (!s.els.includes(e)) e.classList.add('isaac-hide');
    }
    for (const e of s.els) e.classList.remove('isaac-hide');
    return Math.max(0, height() - base);
  });
  for (const [e, wasHidden] of saved) {
    if (wasHidden) e.classList.add('isaac-hide'); else e.classList.remove('isaac-hide');
  }
  return {
    base,
    full: height(),
    sections: subs.map((s, i) => ({idx: i, title: s.title, level: s.level,
                                   h: hs[i], para: s.para, els: s.els.length,
                                   tag: s.head.tagName})),
  };
}
"""

# 按下标隐藏整棵子树；顺带插入变体总览表
APPLY_JS = """
(args) => {
  const hideIdx = args[0];
  const variants = args[1];
  const root = document.querySelector('.mw-parser-output');
  const heads = Array.from(root.querySelectorAll('h2,h3,h4,h5,h6'));
  const lvl = (el) => Number(el.tagName[1]);
  const norm = (h) => h.innerText.replace(/\\[\\s*编辑\\s*\\]/g,'').trim();
  const secs = heads.map((h) => {
    const l = lvl(h);
    const els = [h];
    const nextHead = heads[heads.indexOf(h) + 1];
    let n = h.nextElementSibling;
    while (n) {
      if (n.matches && n.matches('h2,h3,h4,h5,h6') && lvl(n) <= l) break;
      if (nextHead && n.contains && n.contains(nextHead)) break;
      els.push(n);
      n = n.nextElementSibling;
    }
    return {head: h, title: norm(h), els};
  });
  let hidden = 0;
  for (const i of hideIdx) {
    const s = secs[i];
    if (!s) continue;
    for (const e of s.els) e.classList.add('isaac-hide');
    hidden++;
  }
  if (variants && variants.keep !== null && variants.items && variants.items.length) {
    const first = secs[variants.keep] || secs[0];
    const rows = variants.items.map((v) =>
      `<tr><td style="padding:3px 10px;white-space:nowrap">${v.title}</td>`
      + `<td style="padding:3px 10px;opacity:.85">${v.para}</td></tr>`).join('');
    const box = document.createElement('div');
    box.innerHTML =
      '<div style="font-weight:bold;margin:12px 0 4px">其余同类变体'
      + '（各自有独立条目，直接查：以撒 &lt;名称&gt;）</div>'
      + `<table class="wikitable" style="width:100%;border-collapse:collapse">${rows}</table>`;
    first.head.parentNode.insertBefore(box, first.head);
  }
  return hidden;
}
"""

# 只保留某个子树的开头一部分（首个内容节点超预算时）
TRIM_JS = """
(args) => {
  const idx = args[0], budget = args[1], base = args[2];
  const root = document.querySelector('.mw-parser-output');
  const heads = Array.from(root.querySelectorAll('h2,h3,h4,h5,h6'));
  const lvl = (el) => Number(el.tagName[1]);
  const secs = heads.map((h) => {
    const l = lvl(h);
    const els = [h];
    const nextHead = heads[heads.indexOf(h) + 1];
    let n = h.nextElementSibling;
    while (n) {
      if (n.matches && n.matches('h2,h3,h4,h5,h6') && lvl(n) <= l) break;
      if (nextHead && n.contains && n.contains(nextHead)) break;
      els.push(n);
      n = n.nextElementSibling;
    }
    return {els};
  });
  const t = secs[idx];
  if (!t) return null;
  const height = () => Math.round(
    document.querySelector('#mw-content-text').getBoundingClientRect().height);
  let shown = t.els.length;
  while (shown > 1 && height() - base > budget) {
    shown--;
    t.els[shown].classList.add('isaac-hide');
  }
  return {kept: shown, total: t.els.length};
}
"""

# 页面外壳：导航模板（道具图鉴标题条 + 折叠面板 + itemNav 网格）、navbox、折叠清单
CHROME_JS = """
() => {
  const root = document.querySelector('#mw-content-text');
  if (!root) return 0;
  let n = 0;
  for (const c of Array.from(root.querySelectorAll('center'))) {
    const t = c.innerText || '';
    if (t.includes('图鉴') || t.includes('道具池')) {
      c.classList.add('isaac-hide'); n++;
      const nx = c.nextElementSibling;
      if (nx) { nx.classList.add('isaac-hide'); n++; }
    }
  }
  for (const el of Array.from(root.querySelectorAll(
      'table.itemNav, .panel-group, .navbox, table.navbox'))) {
    el.classList.add('isaac-hide'); n++;
  }
  for (const cap of Array.from(root.querySelectorAll('caption'))) {
    if (!/实体列表|列表/.test(cap.innerText || '')) continue;
    const table = cap.closest('table');
    if (!table) continue;
    table.classList.add('isaac-hide'); n++;
    const wrap = table.closest('.table-responsive');
    if (wrap) { wrap.classList.add('isaac-hide'); n++; }
  }
  return n;
}
"""

# 宽表格：按卡片宽度写死像素宽 + 固定列宽 + 允许换行
FIX_TABLES_JS = """
() => {
  const root = document.querySelector('.mw-parser-output');
  if (!root) return 0;
  const cardW = Math.round(
    document.querySelector('#mw-content-text').getBoundingClientRect().width) - 24;
  const px = cardW + 'px';
  let n = 0;
  for (const el of root.querySelectorAll('.table-responsive, [class*="table-wrapper"]')) {
    el.style.setProperty('overflow', 'visible', 'important');
    el.style.setProperty('width', px, 'important');
    el.style.setProperty('max-width', px, 'important');
    n++;
  }
  for (const d of root.querySelectorAll('div')) {
    const cs = getComputedStyle(d);
    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') {
      d.style.setProperty('overflow', 'visible', 'important');
      d.style.setProperty('width', px, 'important');
      d.style.setProperty('max-width', px, 'important');
      n++;
    }
  }
  for (const t of root.querySelectorAll('table')) {
    if (t.matches('.collectiblepos, .itemNav, .infobox, [class*="infobox"]')) continue;
    t.style.setProperty('width', px, 'important');
    t.style.setProperty('max-width', px, 'important');
    t.style.setProperty('min-width', '0', 'important');
    t.style.setProperty('table-layout', 'fixed', 'important');
    n++;
  }
  for (const c of root.querySelectorAll('table th, table td')) {
    c.style.setProperty('white-space', 'normal', 'important');
    c.style.setProperty('word-break', 'break-word', 'important');
  }
  return n;
}
"""

# 整页滚一遍：Anm2 贴图只在滚进视口时才绘制
SCROLL_ALL_JS = """
async () => {
  const h = document.documentElement.scrollHeight;
  for (let y = 0; y < h; y += 500) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 110));
  }
  window.scrollTo(0, h);
  await new Promise((r) => setTimeout(r, 400));
  window.scrollTo(0, 0);
  await new Promise((r) => setTimeout(r, 500));
  return h;
}
"""

MEASURE_JS = (
    "() => { const e = document.querySelector('#mw-content-text');"
    " return e ? Math.round(e.getBoundingClientRect().height) : 0; }"
)

CONTENT_READY_JS = (
    "() => { const r = document.querySelector('.mw-parser-output');"
    " return !!r && (r.querySelectorAll('h2,h3,h4,h5,h6').length > 0"
    " || r.innerHTML.length > 20000); }"
)

NOT_FOUND_MARKERS = ("NOT FOUND", "您正在浏览的页面不存在")


@dataclass
class CardResult:
    path: Path
    height: int
    plan: Plan
    sections: list[Section] = field(default_factory=list)
    page_title: str = ""
    en_title: str = ""
    chrome: int = 0
    tables: int = 0


async def _load(browser_wiki, title: str, *, attempts: int = 3) -> bool:
    """导航并等到「真内容」；wiki 会间歇返回未渲染的空壳，检测到就重载。"""
    page = browser_wiki.page
    url = await browser_wiki.page_url(title)
    for _ in range(attempts):
        try:
            await page.goto(url, wait_until="commit", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        for _ in range(40):  # 最多约 20 秒
            await page.wait_for_timeout(500)
            try:
                if await page.evaluate(CONTENT_READY_JS):
                    return True
            except Exception:  # noqa: BLE001
                continue
        log.warning("isaac_wiki: %s 返回空壳，重载", title)
        try:
            await page.reload(wait_until="commit", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
    return False


async def is_not_found(page) -> bool:
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:  # noqa: BLE001
        return False
    return any(m in text for m in NOT_FOUND_MARKERS)


async def render_card(
    browser_wiki,
    title: str,
    out_path: str | Path,
    *,
    page_title: str = "",
    width: int = 760,
    is_boss: bool = False,
    apply_policy: bool = True,
    allow_trim: bool = True,
    keep_idx: list[int] | None = None,
) -> CardResult:
    """渲染卡片。``keep_idx`` 用于「单独看某一节」的卡片（按分节下标）。"""
    page = browser_wiki.page
    await browser_wiki.ensure_session()
    await page.set_viewport_size({"width": width, "height": 1000})

    if not await _load(browser_wiki, title):
        raise RuntimeError(f"wiki 页面未能渲染：{title}")

    await page.wait_for_timeout(1200)
    await page.add_style_tag(content=CARD_CSS)
    await page.add_style_tag(content=HIDE_CLASS_CSS)
    await page.evaluate(TITLEBAR_JS)
    await page.wait_for_timeout(200)

    chrome = await page.evaluate(CHROME_JS)
    tables = await page.evaluate(FIX_TABLES_JS)

    plan = Plan()
    sections: list[Section] = []
    if keep_idx is not None:
        # 只留指定分节（含其子树）
        data = await page.evaluate(SECTIONS_JS)
        if data:
            keep = set(keep_idx)
            hide = [i for i in range(len(data["sections"])) if i not in keep]
            await page.evaluate(APPLY_JS, [hide, None])
    elif apply_policy:
        data = await page.evaluate(SECTIONS_JS)
        if data:
            sections = [Section(**{k: s[k] for k in ("idx", "title", "level", "h", "para")})
                        for s in data["sections"]]
            plan = plan_card(sections, data["base"], page_title or title,
                             is_boss=is_boss)
            variants = None
            if plan.variants:
                variants = {"keep": plan.keep_variant, "items": plan.variants}
            await page.evaluate(APPLY_JS, [plan.hide, variants])
            if plan.trim is not None and allow_trim:
                from .policy import BUDGET

                await page.wait_for_timeout(300)
                # 预算只针对「该节点自身的高度」，所以基线用整卡基线
                await page.evaluate(TRIM_JS, [plan.trim, BUDGET, data["base"]])

    await page.evaluate(SCROLL_ALL_JS)
    await page.wait_for_timeout(400)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    locator = page.locator(CONTENT_SELECTOR)
    await _shoot(page, locator, out)

    height = await page.evaluate(MEASURE_JS)
    en_title = ""
    try:
        en_title = await page.evaluate(
            "() => { const b=document.querySelector('.isaac-titlebar div:nth-child(2)');"
            " return b ? b.innerText.trim() : ''; }"
        )
    except Exception:  # noqa: BLE001
        pass
    return CardResult(path=out, height=height, plan=plan, sections=sections,
                      page_title=page_title or title, en_title=en_title,
                      chrome=chrome, tables=tables)


async def _shoot(page, locator, out: Path) -> None:
    """截图 ``#mw-content-text``。

    优先走 CDP：Playwright 的元素截图会等 ``document.fonts.ready``，而 wiki 上有
    字体一直加载不完，会卡到超时。CDP 直接按元素矩形截图，不等字体。
    """
    if await locator.count() == 0:
        await page.screenshot(path=str(out), full_page=True)
        return
    try:
        box = await locator.first.bounding_box()
        if box and box["width"] > 0 and box["height"] > 0:
            cdp = await page.context.new_cdp_session(page)
            res = await cdp.send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": True,
                    "clip": {
                        "x": box["x"],
                        "y": box["y"],
                        "width": box["width"],
                        "height": box["height"],
                        "scale": 1,
                    },
                },
            )
            import base64

            out.write_bytes(base64.b64decode(res["data"]))
            try:
                await cdp.detach()
            except Exception:  # noqa: BLE001
                pass
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("isaac_wiki: CDP 截图失败（%s），回退元素截图", exc)
    await locator.first.screenshot(path=str(out), timeout=60000)
