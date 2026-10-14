# nonebot-plugin-isaac-wiki

查询[以撒的结合中文维基](https://isaac.huijiwiki.com/wiki/首页)的 NoneBot2 插件。

## 用法

```
以撒 硫磺火
```

## 实现说明

wiki 的 MediaWiki API（`/api.php`）被 Cloudflare 保护，普通 HTTP 客户端会被
403 拦截。本插件用一个常驻的无头 Chromium 维持会话：

- API 调用在 wiki 页面内用 `fetch()` 发出，携带浏览器会话；
- 卡片图由该浏览器导航到条目页后，注入卡片样式并截图。

详见 `README` 与 `source.py` / `card.py`。
