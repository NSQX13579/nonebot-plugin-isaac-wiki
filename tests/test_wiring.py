"""接线级单测：盯的是「模块之间怎么调」，不是纯逻辑。

两个线上必挂的 bug 就是从这里漏过去的：

* ``engine`` 调 ``card.render_card`` 时多传了一个签名里没有的 ``expect_heading``；
* ``commands`` 用 ``Config.parse_obj(get_driver().config)`` 读配置，pydantic v2 直接抛
  ValueError/ValidationError。

纯逻辑单测（parse/policy）碰不到这两处，``tools/preview.py`` 又绕过 engine 直接调
``card.render_card``，所以这里的核心手法是 ``create_autospec``：它按真实签名校验
调用，参数对不上立刻炸。测试用的浏览器是假的，不联网。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest import mock

import pytest

from nonebot_plugin_isaac_wiki import card, commands, engine, state, store
from nonebot_plugin_isaac_wiki.card import CardResult
from nonebot_plugin_isaac_wiki.policy import Plan, Section

PAGE_HTML = """
<div class="mw-parser-output">
  <table class="infobox">
    <tr class="infobox-header"><th colspan="2"><big>Brimstone</big></th></tr>
    <tr><th class="infobox-label">类型</th><td class="infobox-data">道具</td></tr>
  </table>
  <p>硫磺火（Brimstone）是一个重生中加入的道具。</p>
</div>
"""


class _FakeWiki:
    """只实现 engine / commands 真正会用到的那几个方法。"""

    def __init__(self, html: str = PAGE_HTML) -> None:
        self.html = html
        self.closed = 0

    async def api(self, params):
        return {"query": {"pages": {"1": {"revisions": [{"revid": 42}]}}}}

    async def resolve(self, title):
        return title, []

    async def search(self, term, limit=10):
        return []

    async def fetch(self, title):
        return type("Page", (), {"html": self.html, "title": title})()

    async def close(self):
        self.closed += 1


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list = []
        self.deleted: list = []

    async def send(self, event, message, **kwargs):
        self.sent.append(message)
        return {"message_id": len(self.sent)}

    async def delete_msg(self, **kwargs):
        self.deleted.append(kwargs.get("message_id"))
        return None


def _section(idx=0, title="效果", level=2, h=300) -> Section:
    return Section(idx=idx, title=title, level=level, h=h, para="")


def _fake_render(sections=None, menu=None, height=888):
    """冒充 card.render_card：真写一个文件出来，免得缓存那步读不到。"""
    secs = sections if sections is not None else [_section()]
    menu = ["效果"] if menu is None else menu

    def _render(browser_wiki, title, out_path, **kwargs):
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-png")
        return CardResult(path=out, height=height,
                          plan=Plan(mode="split", menu=menu, hide=[]),
                          sections=secs, page_title=title)

    return _render


def _fake_render_entry(title, **kwargs):
    """冒充 engine.render_entry（签名只有 title 是位置参数）。"""
    out = store.cache_dir() / "_fake_card.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fake-png")
    return CardResult(path=out, height=888,
                      plan=Plan(mode="split", menu=["效果"], hide=[]),
                      sections=[_section()], page_title=title)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """把引擎状态和缓存目录都换成本用例私有的。"""
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_wiki", _FakeWiki())
    monkeypatch.setattr(engine, "_waiting", 0)
    monkeypatch.setattr(engine, "_last_used", 0.0)
    # 模块级 Lock 会绑定到第一个用到它的 event loop，测试里每个用例都换一个
    monkeypatch.setattr(engine, "_lock", asyncio.Lock())
    state.clear()
    yield tmp_path
    engine._consecutive_failures = 0
    state.clear()


def _group_event(group_id=30000, user_id=20000, text="1"):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent
    from nonebot.adapters.onebot.v11.event import Sender

    return GroupMessageEvent(
        time=0, self_id=10000, post_type="message", message_type="group",
        sub_type="normal", message_id=1, user_id=user_id, group_id=group_id,
        raw_message=text, font=0,
        sender=Sender(user_id=user_id, nickname="tester"),
        message=[], original_message=[],
    )


# --- 渲染接口 ---------------------------------------------------------------

def test_engine_calls_render_card_with_its_real_signature(isolated, monkeypatch):
    """engine 传给 render_card 的每个关键字都必须真的在签名里。"""
    spy = mock.create_autospec(card.render_card, side_effect=_fake_render())
    monkeypatch.setattr(engine.card, "render_card", spy)

    res = asyncio.run(engine.render_entry("硫磺火", width=760, idle_close_minutes=0))

    assert spy.call_count == 1
    assert spy.call_args.args[1] == "硫磺火"
    assert res.plan.menu == ["效果"]


def test_cached_render_keeps_menu_and_sections(isolated, monkeypatch):
    """缓存命中要带回菜单：第二次查询不能「有图没菜单」。"""
    spy = mock.create_autospec(card.render_card, side_effect=_fake_render())
    monkeypatch.setattr(engine.card, "render_card", spy)

    async def twice():
        return (await engine.render_entry("硫磺火", idle_close_minutes=0),
                await engine.render_entry("硫磺火", idle_close_minutes=0))

    first, second = asyncio.run(twice())

    assert spy.call_count == 1                      # 第二次没再渲染
    assert second.plan.menu == first.plan.menu == ["效果"]
    assert [s.title for s in second.sections] == ["效果"]
    assert second.height == first.height == 888
    assert second.path.is_file()


def test_old_cache_without_meta_still_renders_a_card(isolated, monkeypatch):
    """老缓存（只有 PNG、没有元数据）不能变成异常。"""
    key = store.cache_key("硫磺火", 42, engine.POLICY_VERSION, 760)
    store.cache_dir().mkdir(parents=True, exist_ok=True)
    (store.cache_dir() / f"{key}.png").write_bytes(b"old")
    spy = mock.create_autospec(card.render_card, side_effect=_fake_render())
    monkeypatch.setattr(engine.card, "render_card", spy)

    res = asyncio.run(engine.render_entry("硫磺火", idle_close_minutes=0))

    assert spy.call_count == 0
    assert res.plan.menu == []          # 没有菜单，但图能用
    assert res.height == 0


# --- 浏览器回收 -------------------------------------------------------------

def test_idle_expired_predicate(isolated, monkeypatch):
    monkeypatch.setattr(engine, "_wiki", None)
    assert engine._idle_expired(10) is False            # 没有浏览器

    monkeypatch.setattr(engine, "_wiki", _FakeWiki())
    monkeypatch.setattr(engine, "_last_used", time.time() - 11 * 60)
    assert engine._idle_expired(10) is True

    monkeypatch.setattr(engine, "_waiting", 1)          # 有人在排队/渲染
    assert engine._idle_expired(10) is False

    monkeypatch.setattr(engine, "_waiting", 0)
    assert engine._idle_expired(0) is False             # 0 = 不自动回收


def test_watchdog_is_singleton_and_shutdown_stops_it(isolated):
    async def scenario():
        engine.start_watchdog(0)
        assert engine._watchdog is None
        engine.start_watchdog(5)
        first = engine._watchdog
        assert first is not None
        engine.start_watchdog(5)                        # 重复调用不会再多一个
        assert engine._watchdog is first
        await engine.shutdown()
        assert engine._watchdog is None
        assert first.done()

    asyncio.run(scenario())


# --- 配置 -------------------------------------------------------------------

def test_cfg_uses_plugin_config_and_reads_env(monkeypatch):
    monkeypatch.setenv("ISAAC_WIKI_CARD_WIDTH", "812")
    cfg = commands._cfg()
    assert cfg.isaac_wiki_card_width == 812
    assert cfg.isaac_wiki_idle_close_minutes == 10


def test_render_feedback_wires_config_into_engine(isolated, monkeypatch):
    """命令层要把配置（含代理、空闲回收分钟数）原样交给 engine 和浏览器。"""
    monkeypatch.setenv("ISAAC_WIKI_PROXY", "http://127.0.0.1:7890")
    wiki = _FakeWiki()
    seen: dict = {}

    async def fake_get_wiki(headless, timeout, proxy="", channel=""):
        seen["proxy"] = proxy
        return wiki

    monkeypatch.setattr(engine, "_get_wiki", fake_get_wiki)
    spy = mock.create_autospec(engine.render_entry, side_effect=_fake_render_entry)
    monkeypatch.setattr(commands.engine, "render_entry", spy)

    bot = _FakeBot()
    asyncio.run(commands._render_with_feedback(
        bot, _group_event(), "硫磺火", canonical="硫磺火"))

    assert any("📕" in str(m) for m in bot.sent), "没出卡片，命令层在更早的地方挂了"
    assert bot.deleted == [1], "出图后应该撤回「正在查询」的回执"
    kwargs = spy.call_args.kwargs
    assert kwargs["idle_close_minutes"] == 10
    assert kwargs["proxy"] == "http://127.0.0.1:7890"
    assert seen["proxy"] == "http://127.0.0.1:7890", "浏览器那条路也要带上代理"
    assert kwargs["width"] == 760
    assert kwargs["use_cache"] is True


def test_missing_browser_degrades_to_a_text_reply(isolated, monkeypatch):
    """没装 playwright 时要有明确回复，不能静默抛异常。"""
    async def broken_get_wiki(headless, timeout, proxy="", channel=""):
        raise ImportError("No module named 'playwright'")

    monkeypatch.setattr(engine, "_get_wiki", broken_get_wiki)
    bot = _FakeBot()

    asyncio.run(commands._render_with_feedback(bot, _group_event(), "硫磺火"))

    assert len(bot.sent) == 2
    notice, reply = (str(m) for m in bot.sent)
    assert "正在查询" in notice, "回执要在解析之前就发出去"
    assert "playwright" in reply
    assert "isaac.huijiwiki.com" in reply
    assert bot.deleted == [1]


def test_failure_reply_carries_the_real_cause(isolated, monkeypatch):
    """服务器上报错只有「（RuntimeError）」等于没线索：要带上真实状态码和原因。"""
    async def broken_get_wiki(headless, timeout, proxy="", channel=""):
        raise RuntimeError("wiki api status 403: 'Just a moment...'")

    monkeypatch.setattr(engine, "_get_wiki", broken_get_wiki)
    bot = _FakeBot()

    asyncio.run(commands._render_with_feedback(bot, _group_event(), "爸爸的便条"))

    assert len(bot.sent) == 2
    notice, reply = (str(m) for m in bot.sent)
    assert "正在查询" in notice and "爸爸的便条" in notice
    assert "403" in reply and "RuntimeError" in reply
    assert "selfcheck" in reply


def test_env_hint_maps_common_deploy_problems():
    """环境类故障要给出可操作的提示，认不出来时不许瞎猜。"""
    from nonebot_plugin_isaac_wiki import commands as c
    from nonebot_plugin_isaac_wiki import source

    assert "playwright install chromium" in c._env_hint(
        RuntimeError("Executable doesn't exist at /root/.cache/ms-playwright/x"))
    assert "root" in c._env_hint(
        RuntimeError("Running as root without --no-sandbox is not supported"))
    assert "pip install playwright" in c._env_hint(
        ImportError("No module named 'playwright'"))
    assert c._env_hint(RuntimeError("wiki api status 403: 'Just a moment...'")) == ""
    assert "ISAAC_WIKI_PROXY" in c._env_hint(
        RuntimeError("Cloudflare 挑战未通过（服务器出网 IP 被判成机器人）"))

    # 服务器以 root 跑时，启动参数里必须自带 --no-sandbox，否则 Chromium 直接拒绝启动
    args = source.launch_args()
    assert "--disable-blink-features=AutomationControlled" in args


def test_proxy_reaches_browser_launch_options():
    """代理要真的落到 playwright 的启动参数上（页面内 fetch 也跟着走）。"""
    from nonebot_plugin_isaac_wiki import source

    plain = source.launch_options(True)
    assert "proxy" not in plain
    assert plain["headless"] is True

    with_proxy = source.launch_options(True, "http://127.0.0.1:7890")
    assert with_proxy["proxy"] == {"server": "http://127.0.0.1:7890"}

    with_channel = source.launch_options(True, "", "chrome")
    # Windows 本地会优先复用系统 Edge，其它平台才用 channel
    if source.system_edge():
        assert "channel" not in with_channel
    else:
        assert with_channel["channel"] == "chrome"


def test_clean_ua_drops_the_headless_token():
    """默认 UA 里的 HeadlessChrome 是 Cloudflare 的明确信号，必须清掉。"""
    from nonebot_plugin_isaac_wiki import source

    headless_ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36")

    class _FakePage:
        async def evaluate(self, expr):
            return headless_ua

    class _FakeContext:
        async def new_page(self):
            return _FakePage()

        async def close(self):
            pass

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            return _FakeContext()

    wiki = source.BrowserWiki(headless=True)
    wiki._browser = _FakeBrowser()
    ua = asyncio.run(wiki._clean_ua())

    assert "Headless" not in ua
    assert "Chrome/141" in ua


def test_stealth_script_is_a_plain_script_and_driver_defaults():
    """隐身脚本必须能被 add_init_script 直接用；没装 patchright 时用 playwright。"""
    from nonebot_plugin_isaac_wiki import source

    assert not source.STEALTH_JS.lstrip().startswith("("), "别写成箭头函数，那样不会执行"
    assert "UNMASKED_RENDERER_WEBGL" in source.STEALTH_JS
    assert "37446" in source.STEALTH_JS

    _, driver = source.playwright_api()
    assert driver in ("patchright", "playwright")


# --- 缓存清理 ---------------------------------------------------------------

def test_prune_removes_meta_together_with_card(isolated):
    key = store.cache_key("硫磺火", 1, engine.POLICY_VERSION, 760)
    src = isolated / "src.png"
    src.write_bytes(b"x" * 4096)
    store.put_cached(key, src, limit_mb=500)
    store.put_meta(key, {"plan": {"menu": ["效果"]}})
    assert store.get_meta(key) is not None

    store.prune_cache(limit_mb=0)

    assert not (store.cache_dir() / f"{key}.png").exists()
    assert not store.meta_path(key).exists()


def test_prune_removes_orphan_meta(isolated):
    ghost = store.cache_dir() / "ghost.json"
    ghost.write_text("{}", encoding="utf-8")
    store.prune_cache()
    assert not ghost.exists()
