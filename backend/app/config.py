import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
# Export .env into the process so SDKs that read os.environ (ANTHROPIC_API_KEY) see it too.
load_dotenv(ENV_FILE)
for _key in ("ANTHROPIC_API_KEY",):  # the SDK treats an empty key as set; the other settings treat empty as off
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

    # Face matching: 128-number descriptors from face-api, compared by Euclidean distance.
    # <= match is a confident match; match..unsure is shown as "not sure"; beyond is unknown.
    # Tuned on stressed photos: two different people came as close as 0.47, so a name is only given at <= 0.45.
    face_match_threshold: float = 0.45
    face_unsure_threshold: float = 0.60
    face_match_margin: float = 0.06  # best match must beat the runner-up by this much
    face_lookalike_threshold: float = 0.40  # refuse to enrol a face this close to someone already in the family

    # WebRTC relay for strict networks (college Wi-Fi). Comma-separated turn: URLs; STUN is always included.
    turn_urls: str | None = None
    turn_username: str | None = None
    turn_credential: str | None = None

    # Firebase web config, injected into the served page at runtime (so one build works anywhere).
    firebase_web_api_key: str | None = None
    firebase_app_id: str | None = None
    firebase_auth_domain: str | None = None

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
