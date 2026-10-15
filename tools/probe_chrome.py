"""诊断：卡片顶部还浮着哪些站点元素（用来校准 ``card.CHROME_JS``）。

用法（仓库根目录）::

    python tools/probe_chrome.py C118 硫磺火

会列出页面上「绝对/固定定位或浮动」且落在卡片顶部的元素（标签、类名、矩形、
文本），以及类名里带 dlc/badge/tag 的元素。chrome 清理规则是启发式的，wiki
改版后靠它决定该补哪条规则。需要联网。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PKG = "nonebot_plugin_isaac_wiki"
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_ROOT / _PKG)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = _pkg

card = importlib.import_module(f"{_PKG}.card")
source = importlib.import_module(f"{_PKG}.source")

PROBE_JS = """
() => {
  const root = document.querySelector('#mw-content-text');
  if (!root) return null;
  const rr = root.getBoundingClientRect();
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {top: Math.round(r.top - rr.top), left: Math.round(r.left - rr.left),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  const floating = [];
  for (const el of Array.from(root.querySelectorAll('*'))) {
    const cs = getComputedStyle(el);
    const abs = cs.position === 'absolute' || cs.position === 'fixed';
    const floated = cs.cssFloat === 'right' || cs.cssFloat === 'left';
    if (!abs && !floated) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) continue;
    if (r.bottom < rr.top - 40 || r.top > rr.top + 240) continue;
    const tips = Array.from(el.querySelectorAll('[title]'))
      .map((n) => (n.getAttribute('title') || '').trim()).filter(Boolean);
    floating.push({tag: el.tagName, cls: String(el.className || '').slice(0, 60),
                   pos: cs.position, float: cs.cssFloat, ...box(el),
                   text: (el.innerText || '').trim().slice(0, 24),
                   tips: tips.slice(0, 4),
                   html: el.outerHTML.slice(0, 400)});
  }
  const badges = [];
  for (const el of Array.from(document.querySelectorAll(
      '.dlc, [class*="dlc"], [class*="badge"], [class*="Badge"], [class*="tag-"]'))) {
    badges.push({tag: el.tagName, cls: String(el.className || '').slice(0, 60),
                 pos: getComputedStyle(el).position, float: getComputedStyle(el).cssFloat,
                 ...box(el), text: (el.innerText || '').trim().slice(0, 24),
                 html: el.outerHTML.slice(0, 160)});
  }
  // 全站通知 / 弹层：固定定位的浮层，以及正文里带「我知道了」这类字样的元素
  const notices = [];
  for (const el of Array.from(document.querySelectorAll('body *'))) {
    const cs = getComputedStyle(el);
    const t = (el.innerText || '').trim();
    const fixed = cs.position === 'fixed';
    const looksNotice = /全站通知|我知道了|详情请点击|本站讯/.test(t) && t.length < 800;
    if (!fixed && !looksNotice) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) continue;
    notices.push({tag: el.tagName, cls: String(el.className || '').slice(0, 90),
                  id: el.id || '', pos: cs.position, z: cs.zIndex,
                  rect: [Math.round(r.left), Math.round(r.top),
                         Math.round(r.width), Math.round(r.height)],
                  text: t.slice(0, 36), html: el.outerHTML.slice(0, 220)});
  }
  return {cardW: Math.round(rr.width), cardH: Math.round(rr.height),
          floating, badges, notices};
}
"""


async def probe(wiki, title: str) -> None:
    print(f"\n=== {title}")
    if not await card._load(wiki, title):
        print("  页面没能渲染")
        return
    page = wiki.page
    # 弹层常常晚于页面渲染才挂载，多等一会儿再看
    await page.wait_for_timeout(3500)
    await page.add_style_tag(content=card.CARD_CSS)
    await page.add_style_tag(content=card.HIDE_CLASS_CSS)
    data = await page.evaluate(PROBE_JS)
    if not data:
        print("  没有 #mw-content-text")
        return
    print(f"  卡片 {data['cardW']}x{data['cardH']}")
    print(f"  顶部浮动元素（{len(data['floating'])} 个）：")
    for item in data["floating"]:
        print(f"    {item['tag']} .{item['cls']} {item['pos']}/{item['float']} "
              f"top={item['top']} left={item['left']} {item['w']}x{item['h']} "
              f"text={item['text']!r}")
        if item.get("tips"):
            print(f"      title: {' | '.join(item['tips'])}")
        print(f"      {item['html']}")
    print(f"  dlc/badge/tag 类名元素（{len(data['badges'])} 个）：")
    for item in data["badges"]:
        print(f"    {item['tag']} .{item['cls']} {item['pos']}/{item['float']} "
              f"top={item['top']} left={item['left']} text={item['text']!r}")
        print(f"      {item['html']}")
    print(f"  固定定位/通知类元素（{len(data['notices'])} 个）：")
    for item in data["notices"]:
        print(f"    {item['tag']}#{item['id']} .{item['cls']} {item['pos']} z={item['z']} "
              f"rect={item['rect']} text={item['text']!r}")
        print(f"      {item['html']}")


async def main() -> int:
    titles = sys.argv[1:] or ["C118"]
    wiki = source.BrowserWiki(headless=True)
    await wiki.start()
    try:
        for title in titles:
            try:
                await probe(wiki, title)
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {type(exc).__name__}: {exc}")
    finally:
        await wiki.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
