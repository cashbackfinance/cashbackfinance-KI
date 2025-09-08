from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    OPENAI_API_KEY: str
    MODEL_NAME: str = "gpt-4o-mini"
    HUBSPOT_PRIVATE_APP_TOKEN: str | None = None
    ALLOWED_ORIGINS: str = "*"
    SYSTEM_PROMPT: str = (
        "Du bist die KI von Cashback Finance. "
        "Ziel: hilfreiche, klare Beratung und Leadgenerierung. "
        "Wenn der Nutzer keine klare Entscheidung liefert, schlage einen Expertenkontakt vor."
    )

    class Config:
        env_file = ".env"

def get_allowed_origins_list(origins: str) -> List[str]:
    return [o.strip() for o in origins.split(",") if o.strip()]
