from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    """插件配置（写在 .env 里，前缀 ISAAC_WIKI_）。"""

    model_config = ConfigDict(extra="ignore")

    isaac_wiki_headless: bool = True
    isaac_wiki_timeout: float = 45.0
    isaac_wiki_card_width: int = 760
