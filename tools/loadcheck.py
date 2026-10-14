"""真实加载自检：在最小 NoneBot 环境里加载插件，检查接线。

不连接 QQ、不发送任何消息。用法（仓库根目录）::

    pip install -e . nonebot2[fastapi] nonebot-adapter-onebot
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
    __plugin_meta__ = plugin.metadata
    print(f"[ok] 插件已加载：{__plugin_meta__.name}")
    print(f"     用法：{__plugin_meta__.usage}")

    # 响应器探针
    from nonebot.matcher import matchers

    groups = {key: value for key, value in matchers.items() if value}
    total = sum(len(group) for group in groups.values())
    print(f"[ok] 已注册响应器：{total} 个")
    for key, group in groups.items():
        for matcher in group:
            print(f"     - #{key}  priority={matcher.priority}  block={matcher.block}")
    if total == 0:
        return _fail("没有注册到任何响应器")
    print("[ok] 自检通过（未连接 QQ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
