"""policy.py 的纯逻辑单测（用真实测到的分节结构造数据，不联网）。"""

from nonebot_plugin_isaac_wiki.policy import Section, plan_card


def sec(idx, title, level, h, para=""):
    return Section(idx=idx, title=title, level=level, h=h, para=para)


def test_references_never_appear_even_when_not_split():
    sections = [
        sec(0, "效果", 2, 300),
        sec(1, "引用和注释", 2, 120),
    ]
    plan = plan_card(sections, base=600, page_title="幸运脚")
    assert plan.mode == "full"
    assert plan.hide == [1]          # 只有引用被隐藏
    assert plan.menu == []


def test_small_entry_is_not_split():
    sections = [sec(0, "效果", 2, 300), sec(1, "注意", 2, 200)]
    plan = plan_card(sections, base=1400, page_title="幸运脚")
    assert plan.mode == "full"


def test_aggregation_collapses_other_variants():
    sections = [
        sec(0, "类型", 2, 400),
        sec(1, "箱子", 2, 900),
        sec(2, "石头箱子", 2, 900),
        sec(3, "金箱子", 2, 900),
        sec(4, "红箱子", 2, 900),
    ]
    plan = plan_card(sections, base=3600, page_title="箱子")
    assert plan.mode == "split"
    assert plan.keep_variant == 1
    assert plan.collapsed == [2, 3, 4]
    assert len(plan.variants) == 3
    assert 2 in plan.hide and 3 in plan.hide and 4 in plan.hide


def test_boss_phases_kept_and_version_goes_to_menu():
    """妈心：正常版本的阶段保留，陵墓版本整棵子树（含子节）进菜单。"""
    sections = [
        sec(0, "妈妈的心脏", 2, 4500),
        sec(1, "行动方式", 3, 2715),
        sec(2, "第1阶段", 4, 779),
        sec(3, "第2阶段", 4, 851),
        sec(4, "陵墓版本", 3, 1495),
        sec(5, "第1阶段", 4, 500),
        sec(6, "第2阶段", 4, 578),
        sec(7, "它还活着", 2, 804),
        sec(8, "注意", 2, 284),
        sec(9, "可解锁成就", 2, 1314),
        sec(10, "画廊", 2, 379),
        sec(11, "轶事", 2, 285),
        sec(12, "引用和注释", 2, 298),
    ]
    plan = plan_card(sections, base=1950, page_title="实体/78", is_boss=True)
    assert plan.mode == "split"
    # 正常版本的阶段不被隐藏
    assert 2 not in plan.hide and 3 not in plan.hide
    # 陵墓版本整块进菜单（它下面的子节跟着走）
    assert 4 in plan.hide
    assert "陵墓版本" in plan.menu
    # 替代形态「它还活着」也进菜单
    assert 7 in plan.hide and "它还活着" in plan.menu
    # 轶事+画廊 合并成一项
    assert 10 in plan.hide and 11 in plan.hide
    assert "轶事+画廊" in plan.menu
    # 引用/注释 永远不出现
    assert 12 in plan.hide


def test_protection_only_applies_to_boss_pages():
    sections = [
        sec(0, "效果", 2, 5000),
        sec(1, "变种", 2, 4000),
    ]
    non_boss = plan_card(sections, base=800, page_title="某道具")
    boss = plan_card(sections, base=800, page_title="实体/9", is_boss=True)
    # 非头目页：「变种」不会因为保护而整块留下
    assert "变种" in non_boss.menu
    # 头目页：「变种」受保护，留在卡片里
    assert "变种" not in boss.menu


def test_giant_first_section_is_trimmed():
    """K44：首个内容节点就是巨型节 -> 只留开头。"""
    sections = [sec(0, "效果", 2, 5000)]
    plan = plan_card(sections, base=800, page_title="K44")
    assert plan.trim == 0
    assert "效果" in plan.menu


def test_boss_pages_never_trim():
    """头目页的首个内容节点既不裁剪也不移出（妈腿页首节会被误测成巨型节）。"""
    sections = [sec(0, "妈妈", 2, 4252), sec(1, "行动方式", 2, 1097)]
    plan = plan_card(sections, base=169, page_title="实体/45", is_boss=True)
    assert plan.trim is None
    assert 0 not in plan.hide
    assert "妈妈" not in plan.menu
