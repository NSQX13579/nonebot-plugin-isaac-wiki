"""离线卡片预览：拉真实数据出卡片图，不发 QQ。

用法（仓库根目录）::

    python tools/preview.py 硫磺火
    python tools/preview.py C118 以撒 地下室 幸运脚

输出图片在 ``previews/`` 目录下。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 只加载纯逻辑子模块，避开插件包的 __init__（它会注册命令、需要 NoneBot
# 运行时），这样预览工具不依赖 NoneBot。
import importlib  # noqa: E402
import sys as _sys  # noqa: E402
import types  # noqa: E402

_PKG = "nonebot_plugin_isaac_wiki"
if _PKG not in _sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(ROOT / _PKG)]  # type: ignore[attr-defined]
    _sys.modules[_PKG] = _pkg

parse = importlib.import_module(f"{_PKG}.parse")
card = importlib.import_module(f"{_PKG}.card")
source = importlib.import_module(f"{_PKG}.source")
render_card = card.render_card
BrowserWiki = source.BrowserWiki

OUT_DIR = ROOT / "previews"


def _slug(title: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
    return safe or "page"


async def show(wiki: BrowserWiki, term: str) -> bool:
    print(f"\n=== {term} ===")
    t0 = time.perf_counter()
    canonical, chain = await wiki.resolve(term)
    t_resolve = time.perf_counter() - t0
    if chain:
        print(f"redirect: {' -> '.join(chain)}")
    if not canonical:
        print("  未找到该条目（标题解析为空）")
        hits = await wiki.search(term, limit=8)
        for i, hit in enumerate(hits, 1):
            print(f"  {i}. {hit.title}  {hit.snippet[:60]}")
        return False

    print(f"canonical title: {canonical}")
    page = await wiki.fetch(canonical)
    t_fetch = time.perf_counter() - t0
    info = parse.infobox(page.html)
    print(f"  infobox title: {info.title} / {info.subtitle}")
    for name, value in info.rows:
        print(f"    {name}: {value}")
    disamb = parse.disambiguation_link(page.html)
    print(f"  disambiguation: {disamb!r}")
    heads = [f"{s.level}:{s.title}" for s in parse.sections(page.html)]
    print(f"  sections: {heads}")
    print(f"  categories: {page.categories[:6]}")
    lead = parse.lead(page.html, limit=2)
    if lead:
        print(f"  lead: {lead[0][:100]}")

    out = OUT_DIR / f"{_slug(canonical)}.png"
    t_before_card = time.perf_counter()
    await render_card(wiki, canonical, out)
    t_card = time.perf_counter() - t_before_card
    size_kb = out.stat().st_size / 1024
    print(f"  timing: resolve={t_resolve:.1f}s fetch={t_fetch - t_resolve:.1f}s card={t_card:.1f}s")
    print(f"  card -> {out} ({size_kb:.0f} KB)")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="以撒 wiki 卡片预览")
    parser.add_argument("terms", nargs="+", help="条目名（中文名、英文名或内部编码）")
    parser.add_argument("--width", type=int, default=760, help="卡片宽度（像素）")
    parser.add_argument("--profile", action="store_true", help="打印渲染各阶段耗时")
    args = parser.parse_args()

    headless = os.environ.get("ISAAC_PREVIEW_HEADFUL") != "1"
    wiki = BrowserWiki(headless=headless)
    await wiki.start()
    try:
        for term in args.terms:
            try:
                await show(wiki, term)
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {type(exc).__name__}: {exc}")
    finally:
        await wiki.close()
    print(f"\n图片目录：{OUT_DIR}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
