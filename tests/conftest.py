"""测试环境：导入插件包会注册命令，需要先初始化一个最小 NoneBot。

只做初始化，不启动服务器、不连接任何平台。
"""

import nonebot

try:
    nonebot.init(driver="~fastapi")
except Exception:  # noqa: BLE001 - 已初始化则忽略
    pass
