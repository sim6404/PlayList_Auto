"""
Velvet Radio — pytest 공통 픽스처
"""
import os
import pytest
from pathlib import Path


# 테스트 환경변수 설정 (실제 API 키 없이도 임포트 가능하도록)
@pytest.fixture(autouse=True, scope="session")
def set_test_env():
    """모든 테스트에 가짜 환경변수 주입"""
    env_vars = {
        "ANTHROPIC_API_KEY": "sk-ant-test-key",
        "SUNO_API_KEY": "test-suno-key",
        "SUNO_API_BASE_URL": "https://api.test.suno.ai/v1",
        "IMAGE_API_KEY": "test-image-key",
        "IMAGE_API_URL": "https://api.test.flux.ai/v1",
        "YOUTUBE_CLIENT_ID": "test-client-id",
        "YOUTUBE_CLIENT_SECRET": "test-client-secret",
        "YOUTUBE_REFRESH_TOKEN": "test-refresh-token",
        "GDRIVE_FOLDER_ID": "test-folder-id",
        "TELEGRAM_BOT_TOKEN": "123456789:test-bot-token",
        "TELEGRAM_CHAT_ID": "-1001234567890",
        "MASTER_PASSWORD": "test-master-pass",
        "DASHBOARD_SECRET_KEY": "test-secret-key",
        "N8N_PASSWORD": "test-n8n-pass",
    }
    for k, v in env_vars.items():
        os.environ.setdefault(k, v)
    yield


@pytest.fixture
def sample_lyrics() -> str:
    return (
        "[Intro: acoustic guitar]\n\n"
        "[Verse]\n[Soft] Morning spills through curtain lace\n"
        "A coffee cup, a quiet space\n\n"
        "[Chorus]\n[Warm] Stay a little longer here\nIn the light that feels like home\n\n"
        "[Bridge]\n[Whispered] Maybe this is all we need\n\n"
        "[Outro]\n[Soft] Mmm, stay a little longer..."
    )
