import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
# Export .env into the process so SDKs that read os.environ (ANTHROPIC_API_KEY) see it too.
load_dotenv(ENV_FILE)
for _key in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "VOYAGE_API_KEY", "FIREBASE_PROJECT_ID"):
    if os.environ.get(_key) == "":  # blank lines in .env must not shadow other credential sources
        del os.environ[_key]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongodb_db: str = "pehchaan"

    # LLM. auto = Groq when GROQ_API_KEY is set, else Claude (ANTHROPIC_API_KEY / ant auth profile).
    # Set LLM_MODE=offline to force the rule-based fallback agent.
    llm_mode: str = "auto"  # auto | groq | claude | offline
    claude_model: str = "claude-opus-5-5"
    groq_api_key: str | None = None
    # gpt-oss models support Groq's strict JSON-schema output, which the agent relies on.
    groq_model: str = "openai/gpt-oss-120b"
    # Free tier allows 8000 tokens/minute including this reservation; keep prompt + this under it.
    groq_max_tokens: int = 4000

    # Embeddings. voyage-3.5 -> 1024 dims. Without VOYAGE_API_KEY a local
    # hashed embedding of the same width is used so the pipeline still runs.
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5"
    # Keys from dash.voyageai.com: https://api.voyageai.com/v1. Keys from Atlas Model API Keys: https://ai.mongodb.com/v1
    voyage_base_url: str = "https://api.voyageai.com/v1"
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

    # Auth. With FIREBASE_PROJECT_ID set, every request must carry a Firebase ID token
    # (Google / email sign-in for caregivers, anonymous sign-in for paired patient devices).
    # Without it the server runs in "dev" auth mode and accepts unsigned dev tokens - local use only.
    firebase_project_id: str | None = None
    # Let any signed-in caregiver open the seeded demo family (handy for judges).
    demo_open_to_all: bool = True

    @property
    def auth_mode(self) -> str:
        return "firebase" if self.firebase_project_id else "dev"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
