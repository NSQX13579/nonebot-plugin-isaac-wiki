"""parse.py 的纯逻辑单测（用离线 HTML 片段，不联网）。"""

from nonebot_plugin_isaac_wiki import parse

INFOBOX_HTML = """
<div class="mw-parser-output">
  <table class="infobox sloppy">
    <tr class="infobox-header"><th colspan="2"><i>角色形象</i></th></tr>
    <tr><td><img src="x.png"></td></tr>
    <tr class="infobox-header"><th colspan="2">
      <i>鲜血激光柱<br><big>Blood laser barrage</big></i></th></tr>
    <tr><th class="infobox-label">ID</th><td class="infobox-data">118</td></tr>
    <tr><th class="infobox-label">类型</th><td class="infobox-data"><a>道具</a></td></tr>
    <tr><th class="infobox-label">品质</th><td class="infobox-data">4</td></tr>
  </table>
  <ul><li>关于与此词条重名或近似的其他词条，请参阅
    <a href="/wiki/硫磺火_(消歧义)">硫磺火 (消歧义)</a>。</li></ul>
  <p>硫磺火（Brimstone）是一个重生中加入的道具。</p>
  <h2>效果 <span class="mw-editsection">[编辑]</span></h2>
  <ul><li>攻击方式改为蓄力发射鲜血激光柱。</li></ul>
  <h3>注意</h3>
  <p>属于利维坦套装。</p>
</div>
"""


def test_infobox_fields():
    info = parse.infobox(INFOBOX_HTML)
    assert info.title == "鲜血激光柱"
    assert info.subtitle == "Blood laser barrage"
    assert ("ID", "118") in info.rows
    assert ("类型", "道具") in info.rows
    assert ("品质", "4") in info.rows


def test_title_prefers_named_header():
    assert parse.title(INFOBOX_HTML) == "鲜血激光柱"


def test_disambiguation_link():
    assert parse.disambiguation_link(INFOBOX_HTML) == "硫磺火 (消歧义)"


def test_lead_skips_infobox_paragraphs():
    html = INFOBOX_HTML.replace(
        '<tr><td><img src="x.png"></td></tr>',
        '<tr><td><p>第1页、第6行、第15列</p></td></tr>',
    )
    lead = parse.lead(html, limit=2)
    assert lead == ["硫磺火（Brimstone）是一个重生中加入的道具。"]


def test_sections_split_and_strip_edit():
    sections = parse.sections(INFOBOX_HTML)
    titles = [(s.level, s.title) for s in sections]
    assert (2, "效果") in titles
    assert (3, "注意") in titles
    effect = next(s for s in sections if s.title == "效果")
    assert effect.lists == [["攻击方式改为蓄力发射鲜血激光柱。"]]


def test_latex_escapes_are_stripped():
    html = '<div class="mw-parser-output"><p>\\( 1.00 \\) 是基础移速数值</p></div>'
    assert "\\(" not in parse.lead(html)[0]
    assert "1.00" in parse.lead(html)[0]
