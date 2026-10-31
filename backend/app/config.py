import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
# Export .env into the process so SDKs that read os.environ (ANTHROPIC_API_KEY) see it too.
load_dotenv(ENV_FILE)
for _key in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
    if os.environ.get(_key) == "":  # blank lines in .env must not shadow other credential sources
        del os.environ[_key]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongodb_db: str = "pehchaan"

    # LLM (Claude). Credentials resolve from ANTHROPIC_API_KEY / ant auth profile.
    # Set LLM_MODE=offline to force the rule-based fallback agent.
    llm_mode: str = "auto"  # auto | claude | offline
    claude_model: str = "claude-opus-5-5"

    # Embeddings. voyage-3.5 -> 1024 dims. Without VOYAGE_API_KEY a local
    # hashed embedding of the same width is used so the pipeline still runs.
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5"
    embedding_dims: int = 1024

    vector_index_name: str = "memory_vec"
    # Candidates with no neighbour above this similarity are added without asking the LLM.
    reconcile_sim_threshold: float = 0.55

    # Repeat-call guard default, used when no caregiver constraint is set.
    default_min_gap_minutes: int = 30

    # Raw transcript retention (privacy by design).
    transcript_ttl_seconds: int = 30 * 24 * 3600

    timezone: str = "Asia/Kolkata"
    public_base_url: str = "http://localhost:5173"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
