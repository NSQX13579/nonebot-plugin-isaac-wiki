"""离线卡片预览：拉真实数据、走生产代码路径出图，不发 QQ。

用法（仓库根目录）::

    python tools/preview.py 硫磺火 妈心 箱子
    python tools/preview.py --section 妈心 它还活着
    python tools/preview.py --via-engine 硫磺火

默认直接调 ``card.render_card``；``--via-engine`` 改成走 ``engine.render_entry``
（生产路径：缓存 + 元数据 + 菜单），用来验证接线是通的。

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
engine = importlib.import_module(f"{_PKG}.engine")
store = importlib.import_module(f"{_PKG}.store")

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


async def show_via_engine(wiki, term: str, width: int,
                          keep_only: list[str] | None = None) -> None:
    """走生产路径出图：engine.render_entry（含缓存、元数据、菜单）。"""
    print(f"\n=== {term}（生产路径 engine.render_entry）")
    canonical, chain = await wiki.resolve(term)
    if not canonical:
        print("  未命中条目")
        return
    if chain:
        print(f"  redirect: {' -> '.join(chain)}")
    page = await wiki.fetch(canonical)
    boss = parse.is_boss(page.html, canonical)
    keep_idx = None
    if keep_only:
        keep_idx = [i for i, s in enumerate(parse.sections(page.html))
                    if s.title in keep_only]
    print(f"  条目：{canonical}  头目={boss}")

    engine._wiki = wiki          # 复用同一个浏览器会话，别另开一个
    common = dict(headless=wiki.headless, width=width, is_boss=boss,
                  keep_idx=keep_idx, idle_close_minutes=0)
    t0 = time.perf_counter()
    first = await engine.render_entry(canonical, **common)
    print(f"  首次渲染：{time.perf_counter() - t0:.1f}s")

    out_dir = OUT_DIR / "engine"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{_slug(canonical)}.png"
    dst.write_bytes(Path(first.path).read_bytes())
    print(f"  卡片：{dst.name}  {first.height}px  计划={first.plan.mode}"
          f"  隐藏={first.plan.hide}")
    print(f"  菜单（{len(first.plan.menu)} 项）：{' / '.join(first.plan.menu) or '无'}")

    if keep_idx is None:          # 带 keep_idx 的卡片不走缓存，不必再跑一次
        second = await engine.render_entry(canonical, **common)
        same_menu = second.plan.menu == first.plan.menu
        same_sections = [s.title for s in second.sections] == \
            [s.title for s in first.sections]
        print(f"  第二次（缓存）：{Path(second.path).name}"
              f"  菜单 {len(second.plan.menu)} 项  height={second.height}"
              f"  分节 {len(second.sections)} 个")
        print(f"  [{'ok' if same_menu and same_sections else '!!'}]"
              f" 缓存命中仍带菜单与分节（menu={same_menu}, sections={same_sections}）")


async def main() -> int:
    ap = argparse.ArgumentParser(description="以撒 wiki 卡片预览")
    ap.add_argument("terms", nargs="+", help="条目名（中文名/英文名/内部编码）")
    ap.add_argument("--section", nargs="+", help="只渲染指定分节（跟在条目名之后）")
    ap.add_argument("--width", type=int, default=760)
    ap.add_argument("--via-engine", action="store_true",
                    help="改走 engine.render_entry（生产路径，验接线与缓存）")
    args = ap.parse_args()

    headless = os.environ.get("ISAAC_PREVIEW_HEADFUL") != "1"
    wiki = source.BrowserWiki(headless=headless)
    await wiki.start()
    if args.via_engine:
        # 别把缓存写进仓库根目录的 data/，预览产物统一放 previews/
        store.data_dir = lambda: OUT_DIR / "_engine_data"
    try:
        for term in args.terms:
            try:
                if args.via_engine:
                    await show_via_engine(wiki, term, args.width, args.section)
                else:
                    await show(wiki, term, args.section)
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {type(exc).__name__}: {exc}")
    finally:
        await wiki.close()
    print(f"\n图片目录：{OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
