"""数字菜单的会话状态。

两条通路（都在 :mod:`commands` 里实现）：

* **直发数字**：只认原提问者、一次性、跟随 ``menu_ttl``（默认 60 秒）；
* **引用菜单消息**：同群任何人、跟随 ``quote_ttl``（默认 30 分钟），可反复用。

同一个「群 + 用户」只保留最近一次菜单，后发的覆盖先发的——这样就不存在
「两个状态谁生效」的问题。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class MenuItem:
    label: str
    kind: str            # "section" | "entry"
    payload: str = ""    # section: 分节标题；entry: 条目名


@dataclass
class Menu:
    message_id: int
    group_id: int
    user_id: int
    title: str                              # 条目显示名
    canonical: str                          # 条目规范标题
    items: list[MenuItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def pick(self, number: int) -> MenuItem | None:
        if 1 <= number <= len(self.items):
            return self.items[number - 1]
        return None


_by_message: dict[int, Menu] = {}
_active: dict[tuple[int, int], int] = {}     # (group, user) -> message_id
_MAX_MENUS = 200                              # 内存里最多留存的菜单


def register(menu: Menu) -> None:
    _by_message[menu.message_id] = menu
    _active[(menu.group_id, menu.user_id)] = menu.message_id
    _gc()


def by_message(message_id: int, *, ttl: float = 1800) -> Menu | None:
    menu = _by_message.get(message_id)
    if menu is None:
        return None
    if time.time() - menu.created_at > ttl:
        _by_message.pop(message_id, None)
        return None
    return menu


def consume_for_user(group_id: int, user_id: int, *, ttl: float = 60) -> Menu | None:
    """直发数字通路：取走（一次性）并校验有效期。"""
    message_id = _active.pop((group_id, user_id), None)
    if message_id is None:
        return None
    menu = _by_message.get(message_id)
    if menu is None:
        return None
    if time.time() - menu.created_at > ttl:
        return None
    return menu


def peek_for_user(group_id: int, user_id: int, *, ttl: float = 60) -> Menu | None:
    """给「直接打开」用：不消费，只看有没有可用的菜单。"""
    message_id = _active.get((group_id, user_id))
    if message_id is None:
        return None
    menu = _by_message.get(message_id)
    if menu is None or time.time() - menu.created_at > ttl:
        return None
    return menu


def _gc() -> None:
    if len(_by_message) <= _MAX_MENUS:
        return
    for message_id, _ in sorted(_by_message.items(), key=lambda kv: kv[1].created_at):
        _by_message.pop(message_id, None)
        if len(_by_message) <= _MAX_MENUS:
            break
    for key, message_id in list(_active.items()):
        if message_id not in _by_message:
            _active.pop(key, None)


def clear() -> None:
    _by_message.clear()
    _active.clear()
