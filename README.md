# nonebot-plugin-isaac-wiki

查询[以撒的结合中文维基](https://isaac.huijiwiki.com/)的 NoneBot2 插件（OneBot V11）。

## 用法

```
以撒 硫磺火
```

默认是**链接模式**：回一条消息，给条目链接 + 搜索入口，**不联网、不起浏览器**。
想让机器人出卡片图，设 `ISAAC_WIKI_MODE=card`——那需要服务器能访问 wiki
（Cloudflare 会拦机房 IP，先跑 `selfcheck.py` 确认）。

| 命令 | 说明 |
| --- | --- |
| `以撒 <条目>` | 查道具、饰品、卡牌、胶囊、角色、怪物、房间、成就（链接模式只给链接） |
| `以撒帮助` | 用法说明 |
| `以撒状态` | 超管：渲染队列、浏览器、缓存、本群开关 |
| `以撒开关` | 超管：开关本群的以撒查询 |

卡片模式下，卡片下面列出的内容可以回复数字继续看（60 秒内、只认提问者）；引用那条菜单消息
再回数字，同群谁都能用，30 分钟内有效。链接模式下这两条通路不生效。

## 依赖

- `beautifulsoup4`、`nonebot-plugin-localstore`，pip 装插件时会一起带上。
- 出卡片还要 `playwright` 和浏览器：

  ```
  pip install playwright && playwright install chromium
  ```

  没装好时命令会明确回一句「服务器上还没装好 playwright / chromium」并附上条目链接，
  而不是没反应。首次查询要等浏览器启动，慢一点是正常的。

## 配置

写在 bot 的 `.env` 里，前缀 `ISAAC_WIKI_`，全部有默认值：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `ISAAC_WIKI_MODE` | link | `link` 只回链接；`card` 出卡片图（需要能访问 wiki） |
| `ISAAC_WIKI_HEADLESS` | true | 无头浏览器；本地调试设 false 能看到渲染过程 |
| `ISAAC_WIKI_CARD_WIDTH` | 760 | 卡片宽度（像素） |
| `ISAAC_WIKI_TIMEOUT` | 30 | 页面导航超时（秒） |
| `ISAAC_WIKI_RATE_LIMIT` | 3 | 同一人查询冷却（秒） |
| `ISAAC_WIKI_MENU_TTL` | 60 | 直发数字的有效期（秒） |
| `ISAAC_WIKI_QUOTE_TTL` | 1800 | 引用菜单消息的有效期（秒） |
| `ISAAC_WIKI_QUEUE_LIMIT` | 3 | 渲染排队上限，超了直接回绝 |
| `ISAAC_WIKI_CACHE_MB` | 500 | 卡片缓存总量上限 |
| `ISAAC_WIKI_CACHE_TTL_DAYS` | 7 | 缓存有效期（天） |
| `ISAAC_WIKI_IDLE_CLOSE_MINUTES` | 10 | 空闲多久关掉浏览器（0 = 不关） |
| `ISAAC_WIKI_PROXY` | 空 | 浏览器与接口走代理，如 `http://127.0.0.1:7890`；wiki 拦服务器 IP 时用 |
| `ISAAC_WIKI_CHANNEL` | 空 | 用真浏览器内核：装了 Chrome 就填 `chrome`（比自带 chromium 更容易过 Cloudflare） |

## 坑

- wiki 的 API 被 Cloudflare 挡着，普通 HTTP 客户端一律 403，把浏览器 cookie 复制出来也没用
  （`__cf_bm` 绑 TLS 指纹）。所以查询和截图都在同一个常驻 Chromium 里做。
- 正文是 JS 渲染的，偶尔返回空壳，插件会检测并重载；信息框贴图只在滚进视口时才绘制。
- 于是进程会常驻一个浏览器（10 分钟不用自动关，bot 退出时会回收）。改了配置要重启 bot。
- wiki 的 Cloudflare 会按环境判机器人（页面停在「请稍候…」、接口 403）。插件已做三件事：
  清掉 UA 里的 `HeadlessChrome`、抹掉无头环境的机器特征（WebGL/插件/chrome 对象）、
  装上 patchright 时自动换用它。仍被拦时依次试：`pip install patchright &&
  patchright install chromium`（装了自动启用）→ `ISAAC_WIKI_HEADLESS=false` 配 `xvfb-run`
  （有头模式）→ `ISAAC_WIKI_CHANNEL=chrome`（真内核）→ `ISAAC_WIKI_PROXY`（代理）。
  selfcheck 会打印 UA / WebGL renderer / 驱动 / 是否撞挑战页，一眼看是哪种情况。

设计细节、踩过的坑和本地验证方法见 [`docs/HANDOFF.md`](docs/HANDOFF.md)。
