"""卡片裁剪策略（纯逻辑，无 NoneBot / 无浏览器依赖，可离线单测）。

规则来自对 wiki 实际结构的反复实测，要点：

* 引用/注释、导航外壳：任何卡片都不出现；
* 聚合页（平级分节共享后缀，如 箱子/石头箱子/金箱子…）：保留与页面同名的
  那个，其余折成一张总览表（它们各自有独立条目，不进数字菜单）；
* 头目页按**分节树**处理：隐藏一个节点会连它的子树一起隐藏，所以
  「陵墓版本」整块进菜单、它下面的「第N阶段」不会跟正常版本的串台；
* 战斗核心（阶段/行动方式/变种）整棵子树保留；
* 轶事 + 画廊合并成一项进菜单；
* 小条目（整卡 <= SMALL_LIMIT）不拆；
* 只有「第一个内容节点」超预算时才做「保留开头」的裁剪。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- 参数 -------------------------------------------------------------------
BUDGET = 1200        # 信息框+导语之后，主卡允许保留的内容高度（px）
SMALL_LIMIT = 2600   # 整卡不超过它就完全不拆
LIST_LIMIT = 15      # 长列表保留前 N 条

# --- 名单 -------------------------------------------------------------------
# 引用/导航：任何卡片都不出现
REFERENCE_SECTIONS = ("目录", "引用", "引用和注释", "注释和引用")
# 编辑器工具类分节（页面外壳）
CHROME_SECTIONS = frozenset(REFERENCE_SECTIONS) | {"请选择搬运目标"}
# 合并成一项进菜单
MERGE_SECTIONS = ("轶事", "画廊")
# 这些后缀不算「变体」，不能折叠
NO_COLLAPSE_SUFFIXES = frozenset({"阶段", "版本"})
# 头目的替代形态：整块进菜单
ALWAYS_MENU_KEYS = ("陵墓版本", "它还活着")
# 战斗核心：整棵子树保留（仅在头目/怪物页生效）
PROTECT_SECTION_KEYS = ("阶段", "行动方式", "行动模式", "变种")


@dataclass
class Section:
    idx: int
    title: str
    level: int
    h: int
    para: str = ""


@dataclass
class Plan:
    mode: str = "full"                    # full = 不拆；split = 有内容进菜单
    hide: list[int] = field(default_factory=list)
    collapsed: list[int] = field(default_factory=list)
    menu: list[str] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)
    keep_variant: int | None = None
    trim: int | None = None
    suffix: str | None = None

    @property
    def split(self) -> bool:
        return self.mode == "split"


def _parents(levels: list[int]) -> list[int | None]:
    """每个节点的父节点：前面最近的层级更小的那个。"""
    out: list[int | None] = []
    for i, lv in enumerate(levels):
        parent = None
        for j in range(i - 1, -1, -1):
            if levels[j] < lv:
                parent = j
                break
        out.append(parent)
    return out


def plan_card(
    sections: list[Section],
    base: int,
    page_title: str,
    *,
    is_boss: bool = False,
) -> Plan:
    """决定隐藏哪些子树、菜单里放什么。

    ``base`` 是信息框 + 导语的高度（所有分节隐藏时的卡片高度）。
    """
    n = len(sections)
    if n == 0:
        return Plan()

    title = [s.title for s in sections]
    level = [s.level for s in sections]
    h = [s.h for s in sections]
    parent = _parents(level)

    # 引用/导航外壳：任何卡片都不出现（含不裁剪的完整版）
    hide: set[int] = {i for i in range(n) if title[i] in CHROME_SECTIONS}
    menu_idx: list[int] = []
    collapsed: list[int] = []
    variants: list[dict] = []
    keep_variant: int | None = None
    suffix: str | None = None

    # 1) 聚合页：同层共享后缀的最大分组
    for ln in range(4, 1, -1):
        buckets: dict[tuple[int, str], list[int]] = {}
        for i in range(n):
            if len(title[i]) < ln:
                continue
            suf = title[i][-ln:]
            if suf in NO_COLLAPSE_SUFFIXES:
                continue
            buckets.setdefault((level[i], suf), []).append(i)
        best = max(buckets.items(), key=lambda kv: len(kv[1]), default=(None, []))
        if best[0] and len(best[1]) >= 3:
            group = best[1]
            suffix = best[0][1]
            keep_variant = next(
                (i for i in group if title[i] == page_title), group[0]
            )
            collapsed = [i for i in group if i != keep_variant]
            hide |= set(collapsed)
            variants = [
                {"title": title[i], "para": sections[i].para} for i in collapsed
            ]
            break

    # 2) 头目的替代形态（陵墓版本 / 它还活着）整块进菜单
    for i in range(n):
        if any(k in title[i] for k in ALWAYS_MENU_KEYS) or (
            is_boss and ("版本" in title[i] or "陵墓" in title[i])
        ):
            hide.add(i)
            menu_idx.append(i)

    # 3) 轶事 / 画廊合并成一项
    merge_idx = [i for i in range(n) if title[i] in MERGE_SECTIONS]

    def inside_hidden(i: int) -> bool:
        j = parent[i]
        while j is not None:
            if j in hide:
                return True
            j = parent[j]
        return False

    def visible_tops() -> list[int]:
        out = []
        for i in range(n):
            if title[i] in CHROME_SECTIONS or i in hide or inside_hidden(i):
                continue
            if parent[i] is not None and parent[i] not in hide:
                continue
            out.append(i)
        return out

    def subtree_protected(i: int) -> bool:
        if not is_boss:
            return False
        if any(k in title[i] for k in PROTECT_SECTION_KEYS):
            return True
        j = i + 1
        while j < n and level[j] > level[i]:
            if any(k in title[j] for k in PROTECT_SECTION_KEYS):
                return True
            j += 1
        return False

    # 4) 小条目：不拆（此时还没把轶事/画廊移出）
    if not collapsed:
        vis = visible_tops()
        if base + sum(h[i] for i in vis) <= SMALL_LIMIT:
            return Plan(hide=sorted(hide))

    hide |= set(merge_idx)

    # 5) 预算：只看顶层可见节点，受保护的子树整个留下
    used = 0
    trim: int | None = None
    for pos, i in enumerate(visible_tops()):
        protected = subtree_protected(i) or i == keep_variant
        if protected:
            used += h[i]
            continue
        if used + h[i] <= BUDGET:
            used += h[i]
        elif pos == 0 and trim is None:
            trim = i
            menu_idx.append(i)
            used += BUDGET
        else:
            hide.add(i)
            menu_idx.append(i)

    menu_titles: list[str] = []
    for i in menu_idx:
        if inside_hidden(i):
            continue
        if title[i] in MERGE_SECTIONS:
            continue
        menu_titles.append(title[i])
    if merge_idx:
        menu_titles.append("轶事+画廊")

    return Plan(
        mode="split",
        hide=sorted(hide),
        collapsed=collapsed,
        menu=menu_titles,
        variants=variants,
        keep_variant=keep_variant,
        trim=trim,
        suffix=suffix,
    )
