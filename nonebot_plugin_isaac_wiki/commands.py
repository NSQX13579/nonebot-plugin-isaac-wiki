"""NoneBot 命令注册。

注册集中在 :func:`register`，由插件包 ``__init__`` 调用；这样纯逻辑模块
（parse / card / source）可以在没有 NoneBot 运行时的环境里单独导入。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg


def register() -> None:
    isaac = on_command("以撒", aliases={"isaac"}, priority=12)

    @isaac.handle()
    async def _handle(event: GroupMessageEvent, arg: Message = CommandArg()):
        term = arg.extract_plain_text().strip()
        if not term:
            await isaac.finish("用法：以撒 硫磺火")
        # TODO: 接 search/卡图管道；当前仅占位，便于 loadcheck 验证接线
        await isaac.finish(MessageSegment.text(f"收到查询：{term}"))
