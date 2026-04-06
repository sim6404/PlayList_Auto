# ============================================================
#  Velvet Radio — Dockerfile
#  Multi-stage: base → worker / dashboard
# ============================================================

FROM python:3.11-slim AS base

# FFmpeg 설치 (Phase 3 영상 제작 필수)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 캐시 레이어
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# __init__.py 생성 (패키지 인식)
RUN touch src/__init__.py \
    src/common/__init__.py \
    src/phase1_concept/__init__.py \
    src/phase2_music/__init__.py \
    src/phase3_video/__init__.py \
    src/phase4_publish/__init__.py \
    src/phase5_distribute/__init__.py \
    dashboard/__init__.py

# 데이터 디렉토리
RUN mkdir -p data/playlists data/lyrics data/audio data/images \
             data/videos data/exports data/approved data/pending_approval logs

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# ── 워커 이미지 ──────────────────────────────────────────────────
FROM base AS worker
CMD ["python", "-m", "src.phase4_publish.scheduler"]

# ── 대시보드 이미지 ──────────────────────────────────────────────
FROM base AS dashboard
EXPOSE 8080
CMD ["python", "-m", "dashboard.app"]
