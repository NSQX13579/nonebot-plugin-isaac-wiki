"""离线卡片预览：拉真实数据、走生产代码路径出图，不发 QQ。

用法（仓库根目录）::

    python tools/preview.py 硫磺火 妈心 箱子
    python tools/preview.py --section 妈心 它还活着

图片输出到 ``previews/``。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
import time
import types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 只加载子模块，避开插件包的 __init__（它会注册命令、需要 NoneBot 运行时）
_PKG = "nonebot_plugin_isaac_wiki"
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_ROOT / _PKG)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = _pkg

parse = importlib.import_module(f"{_PKG}.parse")
policy = importlib.import_module(f"{_PKG}.policy")
card = importlib.import_module(f"{_PKG}.card")
source = importlib.import_module(f"{_PKG}.source")

OUT_DIR = _ROOT / "previews"


def _slug(title: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
    return safe or "page"


async def show(wiki, term: str, keep_only: list[str] | None = None) -> None:
    keep_idx = None
    print(f"\n=== {term}" + (f"（只看 {'/'.join(keep_only)}）" if keep_only else ""))
    t0 = time.perf_counter()
    canonical, chain = await wiki.resolve(term)
    if chain:
        print(f"  redirect: {' -> '.join(chain)}")
    if not canonical:
        hits = await wiki.search(term, limit=5)
        print("  未命中条目，搜索候选：" + ", ".join(h.title for h in hits))
        return
    page = await wiki.fetch(canonical)
    info = parse.infobox(page.html)
    boss = parse.is_boss(page.html, canonical)
    print(f"  条目：{canonical}  标题：{info.title} / {info.subtitle}  头目={boss}")
    print(f"  分节：{', '.join(s.title for s in parse.sections(page.html))}")
    if keep_only:
        keep_idx = [i for i, s in enumerate(parse.sections(page.html))
                    if s.title in keep_only]

    tag = _slug(canonical) + ("_" + "_".join(_slug(k) for k in keep_only) if keep_only else "")
    out = OUT_DIR / f"{tag}.png"
    t1 = time.perf_counter()
    res = await card.render_card(
        wiki, canonical, out,
        page_title=canonical,
        is_boss=boss,
        keep_idx=keep_idx,
    )
    if os.environ.get("ISAAC_DEBUG") and res.sections:
        print("  浏览器侧分节（下标 / 层级 / 净高度 / 标题）：")
        for s in res.sections:
            mark = "隐藏" if s.idx in res.plan.hide else "保留"
            print(f"    [{s.idx}] L{s.level} h={s.h} {mark} {s.title}")
    from PIL import Image

    h = Image.open(out).height
    print(f"  卡片：{out.name}  {res.height}px（文件 {h}px）"
          f"  外壳清理 {res.chrome} 处，表格 {res.tables} 处")
    print(f"  策略：{res.plan.mode}  隐藏={res.plan.hide}  裁剪={res.plan.trim}")
    if res.plan.menu:
        print(f"  菜单（{len(res.plan.menu)} 项）：" + " / ".join(res.plan.menu))
    if res.plan.variants:
        print(f"  变体总览表：{len(res.plan.variants)} 项，保留本体 idx={res.plan.keep_variant}")
    print(f"  耗时：查询 {t1 - t0:.1f}s，渲染 {time.perf_counter() - t1:.1f}s")


async def main() -> int:
    ap = argparse.ArgumentParser(description="以撒 wiki 卡片预览")
    ap.add_argument("terms", nargs="+", help="条目名（中文名/英文名/内部编码）")
    ap.add_argument("--section", nargs="+", help="只渲染指定分节（跟在条目名之后）")
    ap.add_argument("--width", type=int, default=760)
    args = ap.parse_args()

    headless = os.environ.get("ISAAC_PREVIEW_HEADFUL") != "1"
    wiki = source.BrowserWiki(headless=headless)
    await wiki.start()
    try:
        for term in args.terms:
            try:
                await show(wiki, term, args.section)
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {type(exc).__name__}: {exc}")
    finally:
        await wiki.close()
    print(f"\n图片目录：{OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
