from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    """插件配置（写在 .env 里，前缀 ISAAC_WIKI_）。"""

    model_config = ConfigDict(extra="ignore")

    isaac_wiki_headless: bool = True
    isaac_wiki_timeout: float = 30.0
    isaac_wiki_card_width: int = 760

    # 交互
    isaac_wiki_rate_limit: float = 3.0        # 同一人查询冷却（秒）
    isaac_wiki_menu_ttl: int = 60             # 直发数字的有效期（秒）
    isaac_wiki_quote_ttl: int = 1800          # 引用菜单消息的有效期（秒）
    isaac_wiki_queue_limit: int = 3           # 排队上限，超过就直接回绝

    # 缓存
    isaac_wiki_cache_mb: int = 500            # PNG 缓存总量上限
    isaac_wiki_cache_ttl_days: int = 7        # 缓存有效期（天）

    # 浏览器
    isaac_wiki_idle_close_minutes: int = 10   # 空闲多久关掉浏览器
