"""服务器自检（命令行，不连 QQ、不发消息）：回答「查以撒卡在哪一步」。

在部署目录里直接跑：

    cd /home/goat/src/plugins
    /usr/local/bin/python3.12 nonebot_plugin_isaac_wiki/selfcheck.py

依次检查：playwright / chromium 是否就绪 → 能不能打开 wiki → API 能不能调通 →
能不能抓正文 → 能不能出一张卡片。每一步失败都打印异常类型、消息和完整堆栈，
用来区分「环境没装好」和「wiki 侧挡了」。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import types
import traceback
from pathlib import Path

if __package__ in (None, ""):  # 直接当脚本跑
    _pkg_dir = Path(__file__).resolve().parents[0]
    _pkg_name = _pkg_dir.name
    sys.path.insert(0, str(_pkg_dir.parent))
    # 以「影子包」加载：__init__ 会注册 NoneBot 命令、需要运行时，自检用不上
    _shadow = types.ModuleType(_pkg_name)
    _shadow.__path__ = [str(_pkg_dir)]  # type: ignore[attr-defined]
    sys.modules[_pkg_name] = _shadow
    card = importlib.import_module(f"{_pkg_name}.card")
    source = importlib.import_module(f"{_pkg_name}.source")
else:
    from . import card, source

TITLE = "硫磺火"
failures = 0


def step(label: str) -> None:
    print(f"\n[..] {label}", flush=True)


def ok(detail: str = "") -> None:
    print(f"[ok] {detail}", flush=True)


def bad(exc: BaseException) -> None:
    global failures
    failures += 1
    print(f"[!!] {type(exc).__name__}: {exc}", flush=True)
    traceback.print_exc()


def fail_msg(msg: str) -> None:
    global failures
    failures += 1
    print(f"[!!] {msg}", flush=True)


async def check_browser() -> None:
    step("1/5 浏览器是否就绪")
    try:
        import importlib.metadata as md

        print(f"     playwright {md.version('playwright')}", flush=True)
    except Exception as exc:  # noqa: BLE001
        bad(exc)
        return
    print(f"     启动参数: {' '.join(source.launch_args())}", flush=True)
    edge = source.system_edge()
    if edge:
        # Windows 本地开发会复用系统 Edge，Playwright 自带的 chromium 没装也没关系
        print(f"     Windows 本地：用系统 Edge {edge}", flush=True)
        ok()
        return
    try:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            exe = pw.chromium.executable_path
            exists = Path(exe).exists()
            print(f"     chromium: {exe}\n     存在={exists}", flush=True)
            if not exists:
                fail_msg("Playwright 自带的 chromium 不存在：需要执行 playwright install chromium")
        finally:
            await pw.stop()
        ok()
    except Exception as exc:  # noqa: BLE001
        bad(exc)


async def check_steps() -> None:
    wiki = source.BrowserWiki(headless=True)
    try:
        step("2/5 启动浏览器 + 打开 wiki")
        await wiki.start()
        await wiki.ensure_session()
        url = await wiki.page.evaluate("() => location.href")
        title = await wiki.page.title()
        length = await wiki.page.evaluate(
            "() => document.body ? document.body.innerText.length : 0"
        )
        print(f"     url={url}\n     标题={title}  正文字数={length}"
              f"  拿到正文={wiki.content_ok}", flush=True)
        ok()
    except Exception as exc:  # noqa: BLE001
        bad(exc)
        return

    try:
        step("3/5 MediaWiki API（action=query）")
        data = await wiki.api({
            "action": "query", "titles": TITLE, "prop": "revisions",
            "rvprop": "ids", "redirects": "1",
        })
        print(f"     {data.get('query', {}).get('pages')}", flush=True)
        ok()
    except Exception as exc:  # noqa: BLE001
        bad(exc)

    try:
        step("4/5 抓条目正文（action=parse）")
        page = await wiki.fetch(TITLE)
        print(f"     页面={page.title}  HTML 长度={len(page.html)}"
              f"  分类={len(page.categories)}", flush=True)
        ok()
    except Exception as exc:  # noqa: BLE001
        bad(exc)

    try:
        step("5/5 渲染一张卡片")
        # 跟线上一样先解析成规范标题（别名/重定向都走这条路）
        resolved, chain = await wiki.resolve(TITLE)
        target = resolved or TITLE
        print(f"     {TITLE} → {target}"
              + (f"（重定向：{' -> '.join(chain)}）" if chain else ""), flush=True)
        # 别名直达（不先解析）也要能落地——wiki 会重定向，落地校验不能认死 URL
        if not await card._load(wiki, TITLE):
            fail_msg(f"别名「{TITLE}」直达没能落地（重定向后判定失败？）")
        out = Path(tempfile.gettempdir()) / "isaac_selfcheck.png"
        res = await card.render_card(wiki, target, out, page_title=target, width=760)
        print(f"     {out}  {res.height}px  计划={res.plan.mode}"
              f"  菜单={res.plan.menu}", flush=True)
        ok()
    except Exception as exc:  # noqa: BLE001
        bad(exc)
    finally:
        await wiki.close()


async def main() -> int:
    print("== 以撒 wiki 自检 ==", flush=True)
    await check_browser()
    await check_steps()
    print(f"\n== 结束：{failures} 项失败 ==", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
