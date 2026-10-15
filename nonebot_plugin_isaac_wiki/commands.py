"""命令层：查询、数字回选（直发 + 引用）、直接打开、开关。

注册集中在 :func:`register`，由插件包的 ``__init__`` 调用；这样
parse / policy / card / source 这些纯逻辑模块可以在没有 NoneBot 运行时的
环境里单独导入（预览工具与单测都靠这一点）。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from nonebot import get_driver, get_plugin_config, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message as V11Message,
    MessageSegment,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from . import engine, parse, state, store
from .config import Config

log = logging.getLogger("isaac_wiki")

WIKI_BASE = "https://isaac.huijiwiki.com/wiki/"
HELP = (
    "用法：以撒 <道具/角色/怪物/房间…>\n"
    "例：以撒 硫磺火 · 以撒 D6 · 以撒 妈心\n"
    "查完会出卡片；卡片下面列出的内容可以回复数字看。"
)

_last_call: dict[tuple[int, int], float] = {}


def _cfg() -> Config:
    """读插件配置。

    必须走 ``get_plugin_config``：它会把全局配置 dump 成 dict 再交给插件 Config
    重新校验（并读 .env 里 ISAAC_WIKI_* 的覆盖值）。直接 ``Config.parse_obj(
    get_driver().config)`` 在 pydantic v2 下会抛 ValidationError——全局配置是模型
    实例，不是 dict。
    """
    return get_plugin_config(Config)


def _link(canonical: str) -> str:
    safe = canonical.replace(" ", "_")
    from urllib.parse import quote

    return WIKI_BASE + quote(safe)


def _quoted_id(event: GroupMessageEvent) -> int | None:
    for seg in event.get_message():
        if seg.type == "reply":
            try:
                return int(seg.data.get("id"))
            except (TypeError, ValueError):
                return None
    return None


def _plain(event: GroupMessageEvent) -> str:
    return event.get_message().extract_plain_text().strip()


def _env_hint(exc: Exception) -> str:
    """环境类故障给一句可操作的提示（认不出来就返回空串）。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "no module named" in text and "playwright" in text:
        return "服务器还没装 playwright：pip install playwright"
    if "executable doesn't exist" in text or "missing dependencies" in text:
        return "浏览器或系统库没装好：playwright install chromium（必要时 playwright install-deps）"
    if "no-sandbox" in text or "running as root" in text:
        return "Chromium 在 root 下要关 sandbox：插件已自动加 --no-sandbox，仍报错就查系统库"
    if "cloudflare" in text or "挑战未通过" in text:
        return ("wiki 的 Cloudflare 没放行（服务器被判成机器人）。按顺序试："
                "① 本版已自动抹无头特征 ② pip install patchright && patchright install chromium"
                "（装了会自动启用）③ ISAAC_WIKI_HEADLESS=false 配 xvfb-run 有头跑 "
                "④ ISAAC_WIKI_PROXY=http://127.0.0.1:7890 走代理")
    return ""


def _browser_missing(exc: Exception) -> bool:
    """是不是「浏览器/playwright 没装好」这一类错误。"""
    text = f"{type(exc).__name__}: {exc}"
    return isinstance(exc, ImportError) or bool(_env_hint(exc)) or "playwright" in text.lower()


def _unavailable_text(term: str, exc: Exception) -> str:
    lines = [f"wiki 暂时连不上（{_cause(exc)}），稍后再试。"]
    hint = _env_hint(exc)
    if hint:
        lines.append(hint)
    lines.append(f"先直接看 wiki：{_link(term)}")
    lines.append("服务器上可跑 src/plugins/nonebot_plugin_isaac_wiki/selfcheck.py 看卡在哪一步")
    return "\n".join(lines)


def _cause(exc: Exception) -> str:
    """带上异常内容和状态码，不然「（RuntimeError）」等于没写。"""
    text = " ".join(str(exc).split())
    if len(text) > 300:
        text = text[:300] + "…"
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _failure_text(head: str, exc: Exception) -> str:
    """失败回复：带上真实原因，环境类问题再补一句怎么办。"""
    lines = [f"{head}（{_cause(exc)}）"]
    hint = _env_hint(exc)
    if hint:
        lines.append(hint)
    return "\n".join(lines)


def _is_menu_reply(event: GroupMessageEvent) -> bool:
    text = _plain(event)
    quoted = _quoted_id(event)
    if quoted is not None and state.by_message(quoted) is not None:
        return True
    return bool(re.fullmatch(r"\d{1,2}", text))


menu_reply = on_message(rule=Rule(_is_menu_reply), priority=11, block=False)


async def _send_card(bot: Bot, event: GroupMessageEvent, res, text: str) -> int | None:
    """发「图 + 文」一条消息，返回 message_id。"""
    msg = V11Message(MessageSegment.image(Path(res.path))) + MessageSegment.text("\n" + text)
    try:
        out = await bot.send(event, msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("isaac_wiki: 发图失败 %s", exc)
        await bot.send(event, V11Message(MessageSegment.text(text)))
        return None
    if isinstance(out, dict):
        try:
            return int(out.get("message_id"))
        except (TypeError, ValueError):
            return None
    return None


async def _render_with_feedback(bot: Bot, event: GroupMessageEvent, term: str,
                                *, keep_idx: list[int] | None = None,
                                canonical: str | None = None,
                                section_label: str = "") -> None:
    cfg = _cfg()
    title = canonical or term
    # 回执先发：冷启动要拉浏览器 + 建会话，解析条目这一步本身就可能好几秒；
    # 原来的顺序是「先解析再回执」，解析一挂就变成完全没反应
    notice = await bot.send(event, f"🔍 正在查询「{title}」…\n{_link(title)}")
    notice_id = notice.get("message_id") if isinstance(notice, dict) else None

    page = None
    if canonical is None:
        # 浏览器起不来（没装 playwright/chromium）也要有明确回复，
        # 不能让异常冒到 NoneBot 日志里、用户什么都收不到
        try:
            wiki = await _ensure_wiki_for_meta(cfg)
            canonical, chain = await wiki.resolve(term)
        except Exception as exc:  # noqa: BLE001
            log.exception("isaac_wiki: 浏览器/接口不可用")
            await _withdraw(bot, notice_id)
            await bot.send(event, _unavailable_text(term, exc))
            return
        if not canonical:
            try:
                hits = await wiki.search(term, limit=5)
            except Exception as exc:  # noqa: BLE001
                log.warning("isaac_wiki: 搜索失败 %s", exc)
                hits = []
            if not hits:
                await _withdraw(bot, notice_id)
                await bot.send(event, f"没找到「{term}」。试试直接发条目名，或看 wiki 搜索：{_link(term)}")
                return
            lines = ["没找到精确条目，看看这些："]
            for i, h in enumerate(hits[:5], 1):
                lines.append(f"{i} {h.title}")
            await _withdraw(bot, notice_id)
            await bot.send(event, "\n".join(lines) + f"\n（也可以直接发：以撒 <名称>）")
            return
        title = canonical
        page = await wiki.fetch(canonical)

    try:
        if page is None:
            wiki = await _ensure_wiki_for_meta(cfg)
            page = await wiki.fetch(canonical)
        boss = parse.is_boss(page.html, canonical)
        res = await engine.render_entry(
            canonical,
            headless=cfg.isaac_wiki_headless,
            timeout=cfg.isaac_wiki_timeout,
            width=cfg.isaac_wiki_card_width,
            is_boss=boss,
            keep_idx=keep_idx,
            queue_limit=cfg.isaac_wiki_queue_limit,
            cache_mb=cfg.isaac_wiki_cache_mb,
            cache_ttl_days=cfg.isaac_wiki_cache_ttl_days,
            idle_close_minutes=cfg.isaac_wiki_idle_close_minutes,
            proxy=cfg.isaac_wiki_proxy,
            channel=cfg.isaac_wiki_channel,
            use_cache=keep_idx is None,
        )
    except engine.QueueFull:
        await _withdraw(bot, notice_id)
        await bot.send(event, "前面还有人在查，稍等一下再试～")
        return
    except Exception as exc:  # noqa: BLE001
        await _withdraw(bot, notice_id)
        log.exception("isaac_wiki: 渲染失败")
        await bot.send(event, _failure_text("出图失败", exc) + f"\n{_link(canonical)}")
        return

    await _withdraw(bot, notice_id)

    info = parse.infobox(page.html)
    head = info.title or canonical
    if section_label:
        head = f"{head} · {section_label}"
    lines = [f"📕 {head}", _link(canonical)]
    if res.plan.menu:
        lines.append("还能看（回复数字）：")
        lines += [f"{i} {label}" for i, label in enumerate(res.plan.menu, 1)]

    mid = await _send_card(bot, event, res, "\n".join(lines))
    if mid is not None and res.plan.menu:
        # 菜单项标题 -> 分节下标（同名分节取最先出现的一个）
        by_label: dict[str, int] = {}
        for sec in res.sections:
            by_label.setdefault(sec.title, sec.idx)
        items = [state.MenuItem(label=lab, kind="section", payload=str(by_label[lab]))
                 for lab in res.plan.menu if lab in by_label]
        if not items:
            return
        state.register(state.Menu(
            message_id=mid, group_id=event.group_id, user_id=event.user_id,
            title=head, canonical=canonical, items=items,
        ))


async def _withdraw(bot: Bot, message_id) -> None:
    if message_id is None:
        return
    try:
        await bot.delete_msg(message_id=int(message_id))
    except Exception:  # noqa: BLE001
        pass


async def _ensure_wiki_for_meta(cfg: Config):
    """只在需要 API 时确保浏览器就绪（渲染本身也会用到同一个实例）。"""
    return await engine._get_wiki(
        cfg.isaac_wiki_headless, cfg.isaac_wiki_timeout,
        cfg.isaac_wiki_proxy, cfg.isaac_wiki_channel,
    )


@menu_reply.handle()
async def _handle_number(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = _cfg()
    # 群开关关掉之后，引用旧菜单 / 直发数字也不该再出图
    if not store.is_group_enabled(event.group_id):
        return
    text = _plain(event)
    quoted = _quoted_id(event)
    menu = None
    if quoted is not None:
        menu = state.by_message(quoted, ttl=cfg.isaac_wiki_quote_ttl)
    if menu is None:
        if not re.fullmatch(r"\d{1,2}", text):
            return
        menu = state.consume_for_user(event.group_id, event.user_id,
                                      ttl=cfg.isaac_wiki_menu_ttl)
    if menu is None:
        return
    item = menu.pick(int(text))
    if item is None:
        return
    if item.kind == "section" and item.payload.isdigit():
        await _render_with_feedback(
            bot, event, menu.canonical, canonical=menu.canonical,
            keep_idx=[int(item.payload)], section_label=item.label,
        )


isaac = on_command("以撒", aliases={"isaac", "以撒wiki", "查以撒"}, priority=12)


@isaac.handle()
async def _handle_query(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()) -> None:
    cfg = _cfg()
    term = arg.extract_plain_text().strip()

    if not store.is_group_enabled(event.group_id):
        return
    if term in ("帮助", "help", "?"):
        await isaac.finish(HELP)
    if not term:
        await isaac.finish(HELP)

    # 限流
    key = (event.group_id, event.user_id)
    now = time.time()
    if now - _last_call.get(key, 0.0) < cfg.isaac_wiki_rate_limit:
        await isaac.finish("刚查过啦，缓一缓～")
    _last_call[key] = now

    # 直接打开：查询词命中刚发出的菜单项
    menu = state.peek_for_user(event.group_id, event.user_id,
                               ttl=cfg.isaac_wiki_menu_ttl)
    if menu is not None:
        for item in menu.items:
            if item.label == term and item.payload.isdigit():
                await _render_with_feedback(
                    bot, event, menu.canonical, canonical=menu.canonical,
                    keep_idx=[int(item.payload)], section_label=item.label,
                )
                return

    await _render_with_feedback(bot, event, term)


admin = on_command("以撒状态", priority=12, permission=SUPERUSER)
switch = on_command("以撒开关", priority=12, permission=SUPERUSER)


@admin.handle()
async def _handle_admin(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = _cfg()
    stats = store.cache_stats()
    await admin.finish(
        "以撒 wiki 状态：\n"
        f"渲染队列：{engine.busy_count()} 个等待\n"
        f"浏览器空闲：{int(engine.idle_seconds())} 秒"
        f"（{cfg.isaac_wiki_idle_close_minutes} 分钟不用就关）\n"
        f"缓存：{stats['count']} 张 / {stats['bytes'] / 1048576:.1f} MB\n"
        f"本群：{'开启' if store.is_group_enabled(event.group_id) else '关闭'}"
    )


@switch.handle()
async def _handle_switch(bot: Bot, event: GroupMessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await switch.finish("这个命令要在群里用。")
    enabled = store.toggle_group(event.group_id)
    await switch.finish(f"本群以撒查询：{'已开启' if enabled else '已关闭'}")


def register() -> None:
    """保持与其他插件一致的入口（导入本模块即完成注册）。"""
    try:
        get_driver().on_shutdown(engine.shutdown)
    except Exception as exc:  # noqa: BLE001
        log.debug("isaac_wiki: 注册退出钩子失败 %s", exc)
    log.debug("isaac_wiki: commands registered")
