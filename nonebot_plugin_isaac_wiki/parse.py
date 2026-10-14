"""Pure parsing helpers: rendered wiki HTML -> plain data.

Kept free of NoneBot / playwright imports so it can be unit tested and used
by the offline preview tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag


@dataclass
class Infobox:
    title: str = ""
    subtitle: str = ""
    rows: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Section:
    level: int
    title: str
    paragraphs: list[str] = field(default_factory=list)
    lists: list[list[str]] = field(default_factory=list)


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    text = node.get_text(" ", strip=True)
    # 灰机 wiki 的公式节点渲染成 \( ... \)，去掉转义符
    text = text.replace("\\(", "").replace("\\)", "").replace("\\[", "").replace("\\]", "")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*\[\s*编辑\s*\]\s*$", "", text).strip()


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def infobox(html: str) -> Infobox:
    soup = make_soup(html)
    box = soup.select_one(".infobox")
    if box is None:
        return Infobox()
    info = Infobox()
    # 名字头通常是带 <big> 英文名的那条；图片头没有
    head = box.select_one(".infobox-header:has(big)") or box.select_one(".infobox-header")
    if head is not None:
        parts = [p.strip() for p in head.get_text("\n", strip=True).split("\n") if p.strip()]
        if parts:
            info.title = parts[0]
        if len(parts) > 1:
            info.subtitle = parts[1]
    for row in box.select("tr"):
        label = row.select_one(".infobox-label")
        value = row.select_one(".infobox-data")
        if label is None or value is None:
            continue
        name = _text(label)
        val = _text(value)
        if name and val:
            info.rows.append((name, val))
    return info


def title(html: str) -> str:
    soup = make_soup(html)
    box = soup.select_one(".infobox")
    if box is not None:
        head = box.select_one(".infobox-header:has(big)") or box.select_one(".infobox-header")
        if head is not None:
            first = head.get_text("\n", strip=True).split("\n")
            if first and first[0].strip():
                return first[0].strip()
    heading = soup.select_one("h1")
    return _text(heading)


def disambiguation_link(html: str) -> str | None:
    """Return the disambiguation page title if the entry has one."""
    soup = make_soup(html)
    for link in soup.select(".mw-parser-output a"):
        label = link.get_text(" ", strip=True)
        href = str(link.get("href", ""))
        if "消歧义" in label or "消歧义" in href:
            # 红链没有 title 属性，用链接文字兜底
            return str(link.get("title") or label).strip("[]")
    return None


def lead(html: str, limit: int = 3) -> list[str]:
    """First plain paragraphs of the article body."""
    soup = make_soup(html)
    body = soup.select_one(".mw-parser-output")
    if body is None:
        return []
    out: list[str] = []
    for node in body.find_all("p"):
        # 跳过信息框内部的段落（图鉴位置之类）
        if node.find_parent(class_="infobox") is not None:
            continue
        text = _text(node)
        if len(text) < 8:
            continue
        if text.startswith(("登录", "注册")):
            continue
        # 跳过只有箭头/标题的导航段
        if text.count("←") or text.count("→"):
            continue
        if len(text) < 12:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


_HEADINGS = re.compile(r"^h([1-6])$")


def sections(html: str) -> list[Section]:
    """Split the article body by h2/h3 headings."""
    soup = make_soup(html)
    body = soup.select_one(".mw-parser-output")
    if body is None:
        return []
    out: list[Section] = []
    current: Section | None = None
    for node in body.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol"]):
        match = _HEADINGS.match(node.name)
        if match:
            current = Section(level=int(match.group(1)), title=_text(node))
            out.append(current)
            continue
        if current is None:
            continue
        if node.name == "p":
            text = _text(node)
            if len(text) >= 4:
                current.paragraphs.append(text)
        else:
            items = [_text(li) for li in node.find_all("li", recursive=False)]
            items = [i for i in items if i]
            if items:
                current.lists.append(items)
        # stop at the language/links footer inside lang-links containers
        if node.get("class") and "lang-links" in node.get("class", []):
            break
    return out


def plain_summary(html: str) -> str:
    """A short one-paragraph summary for chat text output."""
    parts = lead(html, limit=1)
    return parts[0] if parts else ""
