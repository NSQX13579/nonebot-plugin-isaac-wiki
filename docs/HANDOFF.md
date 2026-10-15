# 交接说明（给下一个会话的 Codex / 人）

插件：`nonebot-plugin-isaac-wiki`，查询《以撒的结合》中文 wiki（isaac.huijiwiki.com）。
仓库：`D:\Projects\Personal\bot\nonebot-plugin-isaac-wiki`（本地 git，尚未推远端）。

## 一句话现状

功能已实现并跑通（卡片出图、命令、数字回选、缓存、限流、开关、单测、自检）。
**剩一个已知 bug**：标题嵌在容器里的页面，分节划分会算错。

## 数据来源的关键事实（踩过的坑，别再查一遍）

1. `/api.php` 被 Cloudflare 挡；**必须在真实浏览器页面里 `fetch`** 才能调通
   （复制 cookie 给 httpx 也没用，`__cf_bm` 绑 TLS 指纹）。
2. 页面正文是 JS 渲染的，会**间歇返回空壳**（6KB、0 个分节）→ 要检测并重载。
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
- 缓存键 = 标题 + `lastrevid` + 策略版本 + 宽度；500MB LRU + 7 天 TTL。

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
- 单测：`& $py -X utf8 -m pytest -q`（12 项）
- 真实加载自检：`& $py -X utf8 tools\loadcheck.py`
- 出卡片：`& $py -X utf8 tools\preview.py 硫磺火 妈心 箱子 妈腿`
- 只看某一节：`& $py -X utf8 tools\preview.py 妈心 --section 它还活着`

以上命令都需要**联网提权**（访问 wiki）。浏览器用系统 Edge（脚本自动探测）。

## 下一步

1. 修上面那个嵌套标题的分节划分 bug，重跑全量验收
2. 部署到 `goat-repo/src/plugins/` 前：确认服务器已装 `beautifulsoup4` 和
   `playwright`（`/usr/local/bin/python3.12 -m pip list | grep -iE 'beautifulsoup|playwright'`），
   `requirements.txt` 补上 `beautifulsoup4`
3. 卡片顶部还有个 wiki 自带的「道具/卡牌」小标签浮在右上角，可顺手清掉
4. 把用法写进 `pm_config/isaac_wiki.yml`，让它出现在机器人的图片帮助菜单里
5. 推远端 / 上商店（按项目惯例：用户自己在 GitHub 网页或 Desktop 操作）
