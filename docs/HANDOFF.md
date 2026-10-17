# 交接说明（给下一个会话的 Codex / 人）

插件：`nonebot-plugin-isaac-wiki`，查询《以撒的结合》中文 wiki（isaac.huijiwiki.com）。
仓库：`D:\Projects\Personal\bot\nonebot-plugin-isaac-wiki`（本地 git，尚未推远端）。

## 一句话现状

功能已实现，接线级测试补齐（命令 → engine → card 三层都有用例盯签名），生产路径
（`engine.render_entry`）已用真实浏览器验收过。
**源码在插件仓库，部署副本在 `goat-repo/src/plugins/nonebot_plugin_isaac_wiki/`**，
两边要手动保持一致（改完源码记得重新拷贝，SHA256 对一下）。
还没推远端；分节划分的嵌套标题问题仍靠 policy 兜底（见文末）。

## 2026-10-17：默认改成链接模式（因为服务器 IP 被 Cloudflare 判死）

服务器上把能试的都试了，全部停在挑战页（「请稍候…」、正文 121 字、API 403）：
干净 UA、patchright（驱动确认生效）、有头 + xvfb、真 Chrome 内核、curl_cffi（Chrome TLS）。
结论是**出网 IP 被判成机器人，浏览器层无解**。

所以加了一个模式开关，**默认 `ISAAC_WIKI_MODE=link`**：

- `link`：只回一条消息，给条目链接 + `index.php?search=` 搜索兜底，
  **不联网、不起浏览器**，服务器上立刻可用。
- `card`：原来的卡片路径，一行代码没删；哪天服务器换了能过 Cloudflare 的出口
  （或落地了离线快照），把 `.env` 改成 `card` 就能切回来。

链接模式复用了原来的命令名、群开关和超管命令；`以撒状态` 在链接模式下只报模式与本群开关。
离线快照方案评估过（原型能出图，但常用条目压缩后 40–60 MB、还要重做卡片渲染），
规模不值当，先不做。

## 本轮修复（2026-10-15 晚，v0.2.0）

上一轮「功能已跑通」其实是假象——**两个线上必挂的接线 bug**（都是 be55e5b 引入，
3119d99 没碰到）：

1. `commands._cfg()` 写的是 `Config.parse_obj(get_driver().config)`。pydantic v2 下
   全局配置是模型实例、不是 dict，直接抛 ValidationError。`_cfg()` 是三个处理函数的第一行，
   于是「以撒 <词>」、数字回选、引用菜单**全部在进逻辑前就炸**，用户侧表现为没反应
   （只有不读配置的 `以撒状态` / `以撒开关` 还活着）。改用 `nonebot.get_plugin_config(Config)`。
2. `engine.render_entry` 调 `card.render_card(..., expect_heading=None, ...)`，而
   `render_card` 压根没有 `expect_heading` 形参 → 每次渲染都 TypeError，走完「重试 → 重启
   浏览器」后回一句「wiki 暂时连不上」。

为什么没被发现：单测只覆盖 parse/policy 纯逻辑；`loadcheck` 只验证响应器注册成功；
`preview.py` 绕过 engine 和 commands 直接调 `card.render_card`——被验证的恰好是没坏的那半条。

### 补上的功能

- **缓存命中不再丢菜单**：原来只存 PNG，第二次查同一条目「有图没菜单」。现在 `store`
  把 `Plan` + 分节写成同名 `.json` 一起缓存，`engine._cached_result` 读回来；淘汰/清理时
  PNG 与元数据成对删除，并扫掉孤儿元数据。缓存版本 `POLICY_VERSION`（现在 `p3`，
  卡片外观改了也要 +1）。
- **空闲回收**：`ISAAC_WIKI_IDLE_CLOSE_MINUTES` 原先只声明没实现。现在
  `engine.start_watchdog` 起一个全局唯一的空闲看门狗（只在真要渲染时才起），超时就关浏览器；
  `register()` 里注册 `driver.on_shutdown(engine.shutdown)`，bot 退出时停看门狗 + 关浏览器，
  不留孤儿 Chromium。
- **群开关也管数字回选**：`_handle_number` 现在先查 `store.is_group_enabled`，关掉之后引用
  旧菜单 / 直发数字都不再出图。
- **失败降级**：浏览器或 playwright 起不来时，异常原本直接冒进 NoneBot 日志、用户什么都收不到；
  现在会回「服务器上还没装好 playwright / chromium」+ 条目链接（`commands._unavailable_text`）。
- **右上角 DLC 徽章**：那是 wiki 的「资料片归属」图标（`DLC-.png`，tooltip
  「重生，或在胎衣版本被移除的特性」），绝对定位压在道具图右上角，压成卡片后会浮在标题栏上。
  3119d99 的规则只认「短文本」，漏掉了这种 `innerText` 为空的纯图标。现在 `TITLEBAR_JS`
  把它**挪进标题栏右上角**（信息保留、不再浮空）；万一没认出来，`CHROME_JS` 的
  `cornerIcon` 分支还会兜底把它隐藏。同页的「道具」圆角标签是跳分类页的导航，继续隐藏。
- **站点通知盖住卡片**：wiki 的 Naive UI 通知弹层（`.n-notification`，固定定位）**晚于**
  清理脚本才挂载（实测：上一轮出图干净纯属运气，下一轮它直接盖住卡片右半边）。现在三层防御：
  `CARD_CSS` 按类名挡（晚挂载照样挡）、`CHROME_JS` 加「固定定位一律隐藏」、截图前再跑一遍清理。
  顺手把「按文案兜一层」限制在正文之外——正文里出现「本站讯」是正常内容，不能连坐。
- **自己踩的坑**：`cornerIcon` 规则会把我们**自己插进标题栏的 DLC 图标槽位**也当徽章隐藏
  （槽位同样是「绝对定位 + 无文本 + 贴右上角」）。所有清理循环都加了
  `el.closest('.isaac-titlebar')` 例外，改这类启发式规则时记得保留。
- **服务器上「每个条目都失败」怎么查**：失败回复原先只有「（RuntimeError）」，等于没线索。
  现在失败回复带真实原因（状态码/异常消息）+ 环境提示，并 `log.exception` 打完整堆栈；
  `source.launch_args()` 还会自动处理 **root 下 Chromium 拒绝启动**
  （加 `--no-sandbox`，容器里再带 `--disable-dev-shm-usage`）——服务器上最常见的死因。
  另外 `nonebot_plugin_isaac_wiki/selfcheck.py` 可以在服务器上五步定位：
  ```bash
  cd /home/goat/src/plugins && /usr/local/bin/python3.12 nonebot_plugin_isaac_wiki/selfcheck.py
  ```
  它会打印启动参数、是否走代理/用哪个内核、当前 UA、是否撞上挑战页——服务器上排查一次就够。

### 测试

`tests/test_wiring.py`（10 项）盯的是「模块之间怎么调」，核心手法
`unittest.mock.create_autospec`——按真实签名校验调用，参数漂移立刻失败。
把上面两个 bug 改回去会挂 4 项（实测过）。`tools/loadcheck.py` 也补了「真读一次配置」的探针。

## 数据来源的关键事实（踩过的坑，别再查一遍）

1. `/api.php` 被 Cloudflare 挡；**必须在真实浏览器页面里 `fetch`** 才能调通
   （复制 cookie 给 httpx 也没用，`__cf_bm` 绑 TLS 指纹）。
   页内 fetch 仍被 403 时，`source.api()` 会再用**顶层导航**取一次（文档请求更容易过）。
   **UA 是决定性的**：Playwright 默认 UA 里带 `HeadlessChrome`，实测同一台机器同一个 IP，
   默认 UA 会被挑战（页面停在「请稍候…」、正文 121 字），**只要把 `Headless` 字样去掉就放行**。
   所以 `BrowserWiki.start()` 现在先问浏览器自报 UA、清掉 `Headless` 再建 context——
   别改成硬编码的假 UA（版本对不上），也别图省事删掉这一步。
   机房 IP 仍可能被拦，三个旋钮按顺序试：`ISAAC_WIKI_CHANNEL=chrome`（装真 Chrome）、
   `ISAAC_WIKI_HEADLESS=false` + `xvfb-run`（有头）、`ISAAC_WIKI_PROXY`（住宅代理）。
   判断是不是挑战页：看 `wiki.challenged`（selfcheck 第 2 步会打印）与标题是否「请稍候…」。
   另外两招已经默认打开/内置：
   - **抹无头特征**（`source.STEALTH_JS`，add_init_script 注入）：无头 Linux 的 WebGL 会报
     SwiftShader、插件列表为空，这些比 UA 更容易被判机器人。注意它是**语句脚本**，
     不能写成 `() => {}`（那样 add_init_script 只是求值一个函数表达式，等于没跑）。
   - **patchright**：装了（`pip install patchright && patchright install chromium`）就自动
     换用它当驱动，CDP 痕迹更少；`playwright_api()` 负责挑。selfcheck 会打印实际驱动。
2. 页面正文是 JS 渲染的，会**间歇返回空壳**（6KB、0 个分节）→ 要检测并重载。
   注意「空壳」的判据**不能拿 URL 比**：wiki 会把别名重定向（`硫磺火` → `C118`），
   比请求时的 URL 会永远等不到「落地」、白等 60 秒后误报「页面未能渲染」。
   现在改成在旧文档上打哨兵（`window.__isaacPrevNav`）：新文档里哨兵消失 = 已换页。
   排查时可用环境变量 `ISAAC_DUMP_SHELL=<路径>` 把「空壳」HTML 存下来看。
3. `DOMContentLoaded`/`load` 很晚甚至不触发 → 导航用 `wait_until="commit"`。
4. **同一页面只导航一次**，重复导航会打断渲染（只剩信息框）。
5. 信息框贴图是 Anm2 部件画在 `<canvas>` 上的，**只在滚进视口时才绘制** →
   截图前必须整页滚一遍。
6. Playwright 的元素截图会等 `document.fonts.ready`，wiki 上有字体加载不完 →
   用 CDP `Page.captureScreenshot` + clip。
7. 隐藏必须用 `.isaac-hide{display:none!important}`；内联 `display:none` 会被
   卡片样式里的 `display:table!important` 压过。
8. 宽表格包在横向滚动容器里会撑宽 → 按卡片宽度写死像素宽 + `table-layout:fixed`
   + 单元格允许换行。
9. 名字行：道具页在 `.infobox-header:has(big)`，实体页在 `.infobox-title`
   （`.infobox-header` 可能是「实体数据」，别取错）。

## 卡片裁剪规则（已在 `policy.py` 固化）

- **引用/导航永不出现**：`目录/引用/引用和注释/注释和引用/请选择搬运目标`
  ＋ 页面外壳（`itemNav` 图标网格、`道具图鉴` 标题条、`.panel-group`、`.navbox`、
  `caption` 含「实体列表」的折叠表）。
- **聚合页**：平级分节共享后缀且 ≥3 个（箱子/石头箱子/金箱子…，0充能/1充能…）→
  保留与页面同名者，其余折成一张总览表（它们各有独立条目，**不进数字菜单**）。
- **按分节树处理**（不是扁平标题）：隐藏一个节点连其子树一起隐。
- **头目页**（`parse.is_boss`：标题以 `实体/` 开头，或信息框「类型」是 头目/怪物）
  才启用保护名单：`阶段 / 行动方式 / 行动模式 / 变种` 整棵子树保留。
- **替代形态**（`陵墓版本`、`它还活着`）整块进菜单。
- **轶事 + 画廊**合并成一项进菜单。
- **小条目**（整卡 ≤2600px）完全不拆，只附链接。
- **内容预算** 1200px（信息框+导语之后）；只有「第一个内容节点」超预算才做
  「保留开头」的裁剪。

## 交互规则

- 指令：`以撒 <词>`（别名 `isaac` / `以撒wiki` / `查以撒`）、`以撒帮助`、
  `以撒状态`（超管）、`以撒开关`（超管，按群）。
- 出卡片是**图 + 文一条消息**：标题、wiki 链接、菜单（最多 6 项）。
- 数字回选两条通路：**直发数字**（只认原提问者、一次性、60 秒）；
  **引用菜单消息 + 数字**（同群任何人、30 分钟、可反复用）。
  同一「群 + 用户」只留最近一条菜单，后发覆盖。
- **直接打开**：查询词若等于刚收到的菜单项标题 → 直接开那一节；
  若无菜单，但词等于解析到的页面上的某个分节标题 → 也直接开那一节。
- 慢查询先发「正在查询… + 链接」，出图后撤回；失败必须有明确回复。
- 限流 3 秒/人；队列上限 3；连续 2 次渲染失败重启浏览器。
- 缓存 = 键（标题 + `lastrevid` + 策略版本 `p3` + 宽度）+ PNG + 同名 `.json` 元数据；
  500MB LRU + 7 天 TTL，淘汰时成对删除。
- 浏览器空闲 10 分钟自动关（`ISAAC_WIKI_IDLE_CLOSE_MINUTES`，0 = 不关），bot 退出时也会回收。
- 群开关关掉后，「以撒 <词>」和数字回选都不再出图。

## 已解决的坑：嵌套标题导致首节被误测（妈腿）

**症状**：妈腿（`实体/45`）卡片只有 2838px，首节「妈妈」高度被算成 4252px
（实际约 221px），被误判成「巨型首节」裁掉。

**根因**：`card.py` 里 `SECTIONS_JS` / `APPLY_JS` / `TRIM_JS` 用「向后遍历兄弟节点」
划分分节区间：遇到层级 ≤ 自己的**兄弟**标题才停。妈腿页的后续标题**嵌在容器里**
（不是平级兄弟），遍历不会停，首节把整页都圈进去了。

**影响范围**：扫过 6 个头目页（妈心/羔羊/妈腿/撒旦/超级撒旦/实体42）+ 3 个对照页，
**只有妈腿**出现（`妈妈` h=4252、els=11，其它页最大节占比 14%~37%）。

**采用的修法**：在 `policy.plan_card` 里让**头目页的第一个内容节点一律保留**
（既不裁剪也不移出）。头目页的战斗内容本来靠保护名单留下，而首节裁剪那条规则
是为 K44 那种「首个内容节点就是巨型节」的普通条目设计的，在头目页只会误伤。
修完妈腿回到 6982px（保留 妈妈/行动方式/妈妈的践踏/加强版行动方式/变种，
菜单 3 项：可解锁成就 / 注意 / 轶事+画廊）。

**如果以后还有别的页面中招**（症状：某节高度明显偏大、元素数远多于同类页），
根治办法是把分节区间改成按**文档顺序**划分（用 `compareDocumentPosition`），
而不是走兄弟节点——三个 JS 常量要保持同一套逻辑。

## 怎么跑起来验证

环境（沙箱内 `python`/`py` 不可用；venv 放在 TEMP，因为 `D:\venvs\skills` 不可写）：
```powershell
$py = "$env:TEMP\isaac-probe-venv\Scripts\python.exe"     # 已装 playwright / nonebot2 2.4.4 / bs4 / pytest / pillow
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
$env:PYTHONUTF8 = '1'
```
- 单测：`& $py -X utf8 -m pytest -q`（23 项，含 `test_wiring.py` 的接线用例）
- 真实加载自检：`& $py -X utf8 tools\loadcheck.py`（顺带真读一次配置）
- 出卡片：`& $py -X utf8 tools\preview.py 硫磺火 妈心 箱子 妈腿`
- 走生产路径（engine.render_entry：缓存 + 元数据 + 菜单）：
  `& $py -X utf8 tools\preview.py --via-engine 硫磺火`
  （会顺手抽查「标题栏里 DLC 图标已加载、页面上没有残留的固定浮层」）
- 只看某一节：`& $py -X utf8 tools\preview.py 妈心 --section 它还活着`
- 看清卡片顶部还浮着什么（chrome 规则是启发式的，wiki 改版后用它校准）：
  `& $py -X utf8 tools\probe_chrome.py C118`
- 全流程自检（部署到服务器后查故障就用它，不需要 NoneBot）：
  `& $py -X utf8 nonebot_plugin_isaac_wiki\selfcheck.py`

以上命令都需要**联网提权**（访问 wiki）。浏览器用系统 Edge（脚本自动探测）。

## 下一步

1. **部署**：`goat-repo` 里已备好四样东西——`src/plugins/nonebot_plugin_isaac_wiki/`
   （10 个 .py，与源码 SHA256 一致）、`requirements.txt` 的 `beautifulsoup4==4.14.3`、
   `pm_config/isaac_wiki.yml`、`.env.example` 的 `ISAAC_WIKI_*` 段。
   服务器上 `git pull` → `pip install -r requirements.txt`（bs4 其实随 mediawiki 已经在了）
   → 确认 chromium `playwright install chromium` → **重启 `python3.12 bot.py`**（Python 不热加载）。
2. **推远端 / 上商店**（按项目惯例：用户自己在 GitHub 网页或 Desktop 操作）。
   注意这个插件仓库目前**还没有远端**；上商店还需要 PyPI 发版流水线（照 `nonebot-plugin-dsh-release` 那套），
   `PluginMetadata.homepage` 与 `pyproject.toml` 的 `homepage` 现在还是空串。
3. 可选：把分节区间改成按**文档顺序**（`compareDocumentPosition`）划分，根治嵌套标题误测
   （`SECTIONS_JS` / `APPLY_JS` / `TRIM_JS` 三个常量要一起改；目前靠 policy 兜着，线上不受影响）
4. 可选清理：`parse.lead` / `plain_summary` / `sections` / `disambiguation_link`（只有测试引用）、
   `CardResult.en_title`、`PageData.wikitext` / `categories`
