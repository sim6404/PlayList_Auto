"""
Velvet Radio — 설정 로더
.env + config/*.json 을 통합 관리
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# 프로젝트 루트 기준 경로
ROOT = Path(__file__).parent.parent.parent
load_dotenv(ROOT / "config" / ".env", override=True)
load_dotenv(ROOT / ".env", override=False)  # 루트 .env 폴백


class AppConfig(BaseSettings):
    """환경변수 기반 설정 (Pydantic BaseSettings)"""

    # Claude
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    claude_model: str = Field("claude-sonnet-4-20250514", alias="CLAUDE_MODEL")

    # Suno
    suno_api_key: str = Field(..., alias="SUNO_API_KEY")
    suno_api_base_url: str = Field("https://api.sunoapi.org/v1", alias="SUNO_API_BASE_URL")
    suno_model: str = Field("v5", alias="SUNO_MODEL")

    # Image
    image_api_key: str = Field(..., alias="IMAGE_API_KEY")
    image_api_url: str = Field("https://api.apiframe.ai/v1", alias="IMAGE_API_URL")

    # YouTube
    youtube_client_id: str = Field(..., alias="YOUTUBE_CLIENT_ID")
    youtube_client_secret: str = Field(..., alias="YOUTUBE_CLIENT_SECRET")
    youtube_refresh_token: str = Field(..., alias="YOUTUBE_REFRESH_TOKEN")
    youtube_channel_id: str = Field("UC1_f-igasDLBcV3VyxYRfAA", alias="YOUTUBE_CHANNEL_ID")

    # Google Drive
    gdrive_folder_id: str = Field(..., alias="GDRIVE_FOLDER_ID")
    gdrive_credentials_path: str = Field("./config/gdrive_credentials.json", alias="GDRIVE_CREDENTIALS_PATH")

    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(..., alias="TELEGRAM_CHAT_ID")
    telegram_admin_ids: str = Field("", alias="TELEGRAM_ADMIN_IDS")

    # n8n
    n8n_password: str = Field("admin123", alias="N8N_PASSWORD")
    n8n_webhook_base_url: str = Field("http://localhost:5678/webhook", alias="N8N_WEBHOOK_BASE_URL")

    # Dashboard
    dashboard_secret_key: str = Field("changeme-secret", alias="DASHBOARD_SECRET_KEY")
    dashboard_port: int = Field(8080, alias="DASHBOARD_PORT")
    master_password: str = Field(..., alias="MASTER_PASSWORD")

    # Paths
    data_dir: Path = Field(ROOT / "data", alias="DATA_DIR")
    config_dir: Path = Field(ROOT / "config", alias="CONFIG_DIR")
    log_dir: Path = Field(ROOT / "logs", alias="LOG_DIR")

    # Upload Settings
    upload_privacy_default: str = Field("private", alias="UPLOAD_PRIVACY_DEFAULT")
    max_concurrent_suno_jobs: int = Field(3, alias="MAX_CONCURRENT_SUNO_JOBS")
    max_retry_attempts: int = Field(3, alias="MAX_RETRY_ATTEMPTS")

    # Quality Gates
    min_tracks_for_publish: int = Field(15, alias="MIN_TRACKS_FOR_PUBLISH")
    min_duration_seconds: int = Field(150, alias="MIN_DURATION_SECONDS")
    max_duration_seconds: int = Field(270, alias="MAX_DURATION_SECONDS")
    target_lufs_min: float = Field(-14.0, alias="TARGET_LUFS_MIN")
    target_lufs_max: float = Field(-10.0, alias="TARGET_LUFS_MAX")

    class Config:
        populate_by_name = True
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def admin_ids(self) -> list[int]:
        if not self.telegram_admin_ids:
            return []
        return [int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()]


class ChannelDNA:
    """channel_dna.json 로더 및 접근자"""

    def __init__(self, path: Path | None = None):
        p = path or (ROOT / "config" / "channel_dna.json")
        with open(p, encoding="utf-8") as f:
            self._data: dict = json.load(f)

    def __getitem__(self, key: str):
        return self._data[key]

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    @property
    def channel(self) -> dict:
        return self._data["channel"]

    @property
    def music_identity(self) -> dict:
        return self._data["music_identity"]

    @property
    def vocal_personas(self) -> dict:
        return self._data["vocal_personas"]

    @property
    def playlist_structure(self) -> dict:
        return self._data["playlist_structure"]

    @property
    def instrument_map(self) -> dict:
        return self._data["instrument_map"]

    @property
    def seo(self) -> dict:
        return self._data["seo"]


def load_prompt(name: str) -> str:
    """src/phase*/prompts/ 또는 src/common/prompts/ 에서 프롬프트 파일 로드"""
    candidates = [
        ROOT / "src" / "phase1_concept" / "prompts" / f"{name}.txt",
        ROOT / "src" / "phase4_publish" / "prompts" / f"{name}.txt",
        ROOT / "src" / "common" / "prompts" / f"{name}.txt",
    ]
    for c in candidates:
        if c.exists():
            return c.read_text(encoding="utf-8")
    raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {name}")


# 싱글턴
_config: AppConfig | None = None
_dna: ChannelDNA | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def get_dna() -> ChannelDNA:
    global _dna
    if _dna is None:
        _dna = ChannelDNA()
    return _dna


# 편의 접근자
config = get_config()
dna = get_dna()
