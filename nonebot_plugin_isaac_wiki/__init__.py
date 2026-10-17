"""nonebot-plugin-isaac-wiki

查询《以撒的结合》中文 wiki（灰机 wiki）的条目。

数据来源：isaac.huijiwiki.com 的 MediaWiki API。该 API 被 Cloudflare 的
bot 规则保护，普通 HTTP 客户端会被 403 挡住；必须在一个真实浏览器会话里
调用。因此本插件用一个常驻的无头 Chromium 承载「登录会话」，既用来调
API（在页面内 fetch），也用来渲染最终卡片截图。
"""

from nonebot.plugin import PluginMetadata

from .config import Config

__version__ = "0.3.0"

__plugin_meta__ = PluginMetadata(
    name="以撒 wiki 查询",
    description="查询以撒的结合中文 wiki：道具、饰品、卡牌、胶囊、角色、怪物、房间、成就",
    usage=(
        "以撒 <条目>    查询，直接给出 wiki 链接（如：以撒 硫磺火）\n"
        "以撒           用法说明\n"
        "以撒帮助       完整说明\n"
        "以撒状态       超管：运行状态\n"
        "以撒开关       超管：开关本群查询\n"
        "\n"
        "默认是链接模式（不出图）。设 ISAAC_WIKI_MODE=card 且环境能访问 wiki 时才出卡片，"
        "卡片下方的内容可以回复数字查看。"
    ),
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

from .commands import register as _register  # noqa: E402

_register()
