"""真实加载自检：在最小 NoneBot 环境里加载插件，检查接线。

不连接 QQ、不发送任何消息。用法（仓库根目录）::

    pip install -e . "nonebot2[fastapi]" nonebot-adapter-onebot beautifulsoup4 playwright
    python tools/loadcheck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fail(message: str) -> int:
    print(f"[x] {message}")
    return 1


def main() -> int:
    try:
        import nonebot
        from nonebot.adapters.onebot.v11 import Adapter
    except ImportError as exc:
        print(f"[!] 缺少依赖：{exc}")
        print('    请先执行：pip install -e . "nonebot2[fastapi]" nonebot-adapter-onebot')
        return 2

    nonebot.init(driver="~fastapi")
    nonebot.get_driver().register_adapter(Adapter)
    plugin = nonebot.load_plugin("nonebot_plugin_isaac_wiki")
    if plugin is None:
        return _fail("插件加载失败")
    meta = plugin.metadata
    print(f"[ok] 插件已加载：{meta.name if meta else '?'}")
    if meta:
        print(f"     用法：{meta.usage}")

    from nonebot.matcher import matchers

    groups = {k: v for k, v in matchers.items() if v}
    total = sum(len(g) for g in groups.values())
    print(f"[ok] 已注册响应器：{total} 个")
    for key, group in groups.items():
        for matcher in group:
            print(f"     - #{key} priority={matcher.priority} block={matcher.block}")
    if total == 0:
        return _fail("没有注册到任何响应器")

    # 指令前缀探针：列出注册进前缀树、且属于本插件的命令前缀
    from nonebot.rule import TrieRule

    keys = sorted(TrieRule.prefix.keys())
    own = [p for p in keys if ("以撒" in p or "isaac" in p)]
    print(f"[ok] 前缀树共 {len(keys)} 个前缀，抽样：{keys[:6]}")
    print("[ok] 已注册命令前缀：")
    for p in own:
        print(f"     {p!r}")
    if not own:
        return _fail("前缀树里没有本插件的命令")

    # 真读一次配置：这条路径以前写成 Config.parse_obj(get_driver().config)，
    # pydantic v2 下会抛 ValidationError，而「加载成功」是看不出来的
    commands = getattr(getattr(plugin, "module", None), "commands", None)
    if commands is None:
        return _fail("拿不到 commands 模块，没法验证配置解析")
    try:
        cfg = commands._cfg()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读插件配置失败：{type(exc).__name__}: {exc}")
    print(f"[ok] 配置解析成功：宽度 {cfg.isaac_wiki_card_width}px，"
          f"队列 {cfg.isaac_wiki_queue_limit}，"
          f"空闲 {cfg.isaac_wiki_idle_close_minutes} 分钟回收浏览器")
    print("[ok] 自检通过（未连接 QQ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
