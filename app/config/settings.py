from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "agentic-ai-backend"
    redis_url: str = "redis://localhost:6379/0"

    groq_api_key: str = ""
    gemini_api_key: str = ""
    together_api_key: str = ""

    max_retries: int = 3
    step_timeout_seconds: int = 30
    planner_timeout_seconds: int = 20
    search_timeout_seconds: int = 8
    fetch_timeout_seconds: int = 10
    writer_timeout_seconds: int = 40
    stream_idle_timeout_seconds: int = 15
    pending_timeout_seconds: int = 30
    max_task_steps: int = 6
    max_task_chars: int = 2000
    task_ttl_seconds: int = 86400
    max_search_results: int = 3
    max_fetched_pages: int = 2
    enable_event_replay: bool = False

    task_stream_prefix: str = Field(default="task_stream")
    result_stream: str = Field(default="result_stream")
    event_stream_prefix: str = Field(default="event_stream")
    dead_letter_prefix: str = Field(default="dead_letter_stream")


@lru_cache
def get_settings() -> Settings:
    return Settings()
