"""
Velvet Radio — 공통 데이터 모델 (Pydantic v2)
모든 Phase에서 공유하는 핵심 데이터 구조 정의
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class VocalPersona(str, Enum):
    VR_F1 = "warm female, breathy, intimate, subtle vibrato"
    VR_F2 = "bright female, airy, youthful, light falsetto"
    VR_M1 = "smooth male, warm baritone, relaxed delivery"
    VR_M2 = "soft male, gentle tenor, understated emotion"


class Mood(str, Enum):
    COZY            = "cozy"
    NOSTALGIC       = "nostalgic"
    DREAMY          = "dreamy"
    GOLDEN_HOUR     = "golden-hour"
    RAINY_DAY       = "rainy-day"
    SUNDAY_MORNING  = "sunday-morning"
    LATE_NIGHT      = "late-night"
    BREEZY          = "breezy"
    TENDER          = "tender"
    HOPEFUL         = "hopeful"
    MELANCHOLIC     = "melancholic-soft"
    EUPHORIC_GENTLE = "euphoric-gentle"


class ApprovalStatus(str, Enum):
    PENDING   = "pending"
    APPROVED  = "approved"
    REJECTED  = "rejected"
    REVISION  = "revision_requested"


class UploadPrivacy(str, Enum):
    PRIVATE   = "private"
    UNLISTED  = "unlisted"
    PUBLIC    = "public"


class PipelineStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    SKIPPED    = "skipped"


# ─────────────────────────────────────────────
# Phase 1 — 컨셉 기획
# ─────────────────────────────────────────────

class Track(BaseModel):
    order: int = Field(..., ge=1, le=20, description="트랙 순서 (1~20)")
    title: str = Field(..., min_length=2, max_length=80)
    mood: Mood
    sub_genre: str
    bpm: int = Field(..., ge=72, le=108)
    key: str
    vocal: VocalPersona
    hook_priority: bool = False   # 초반 3곡 여부
    concept_note: Optional[str] = None

    @field_validator("order")
    @classmethod
    def validate_hook(cls, v: int, info) -> int:
        return v


class Lyrics(BaseModel):
    track_order: int
    content: str
    language: str = "en"
    char_count: int = 0

    def model_post_init(self, __context) -> None:
        self.char_count = len(self.content)

    @field_validator("content")
    @classmethod
    def must_have_metatags(cls, v: str) -> str:
        required = ["[Verse", "[Chorus]"]
        for tag in required:
            if tag not in v:
                raise ValueError(f"가사에 필수 메타태그 누락: {tag}")
        if len(v) > 3000:
            raise ValueError(f"가사가 3,000자를 초과합니다: {len(v)}자")
        return v


class StylePrompt(BaseModel):
    track_order: int
    prompt: str
    negative: str

    @field_validator("prompt")
    @classmethod
    def prompt_length_ok(cls, v: str) -> str:
        if len(v) > 1000:
            raise ValueError(f"스타일 프롬프트가 1,000자를 초과합니다: {len(v)}자")
        return v


class Playlist(BaseModel):
    id: str             # YYYYMMDD_theme_slug
    theme: str
    concept: str
    tracks: list[Track]
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    @field_validator("tracks")
    @classmethod
    def validate_track_count(cls, v: list) -> list:
        if len(v) < 15 or len(v) > 20:
            raise ValueError(f"트랙 수가 범위를 벗어납니다: {len(v)}개 (15~20 허용)")
        return v


# ─────────────────────────────────────────────
# Phase 2 — 음악 생성
# ─────────────────────────────────────────────

class SunoPayload(BaseModel):
    track_order: int
    lyrics: str
    style_prompt: str
    model: str = "v5"
    instrumental: bool = False
    title: Optional[str] = None


class AudioAsset(BaseModel):
    playlist_id: str
    track_order: int
    title: str
    file_path: str
    duration_seconds: float
    lufs: float
    silence_ratio: float
    clipping: bool
    quality_score: float = 0.0
    selected: bool = False


class QualityReport(BaseModel):
    playlist_id: str
    total_generated: int
    selected_count: int
    failed_tracks: list[int] = []
    assets: list[AudioAsset]
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ─────────────────────────────────────────────
# Phase 3 — 영상 제작
# ─────────────────────────────────────────────

class VideoAsset(BaseModel):
    playlist_id: str
    thumbnail_path: str
    background_path: str
    background_samples: list[str] = Field(default_factory=list)  # 5종 샘플 경로
    spectrum_video_path: str
    subtitle_path: str
    final_video_path: str
    duration_seconds: int
    resolution: str = "1920x1080"
    has_chapters: bool = True


# ─────────────────────────────────────────────
# Phase 4 — SEO + 업로드 + 승인
# ─────────────────────────────────────────────

class SEOMetadata(BaseModel):
    playlist_id: str
    title_en: str
    title_ja: Optional[str] = None
    title_pt: Optional[str] = None
    title_ko: Optional[str] = None
    description_en: str
    description_ja: Optional[str] = None
    description_pt: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    chapter_timestamps: list[str] = Field(default_factory=list)

    @field_validator("title_en")
    @classmethod
    def title_length(cls, v: str) -> str:
        if len(v) > 100:
            raise ValueError(f"제목이 100자를 초과합니다: {len(v)}자")
        return v

    @field_validator("tags")
    @classmethod
    def tags_count(cls, v: list) -> list:
        if len(v) > 500:
            raise ValueError("태그가 너무 많습니다")
        return v


class ApprovalRequest(BaseModel):
    playlist_id: str
    video_path: str
    thumbnail_path: str
    background_samples: list[str] = Field(default_factory=list)   # 5종 배경 샘플 경로
    selected_background_index: int = 0                             # 관리자 선택 (0=sample_1)
    preview_url: Optional[str] = None
    seo: SEOMetadata
    quality_report: QualityReport
    status: ApprovalStatus = ApprovalStatus.PENDING
    master_feedback: Optional[str] = None
    requested_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    reviewed_at: Optional[str] = None


class YouTubeUpload(BaseModel):
    playlist_id: str
    video_path: str
    title: str
    description: str
    tags: list[str]
    thumbnail_path: str
    category_id: str = "10"   # Music
    scheduled_at: Optional[str] = None
    privacy: UploadPrivacy = UploadPrivacy.PRIVATE
    youtube_video_id: Optional[str] = None
    youtube_playlist_id: Optional[str] = None
    upload_status: PipelineStatus = PipelineStatus.PENDING


# ─────────────────────────────────────────────
# 파이프라인 상태 추적
# ─────────────────────────────────────────────

class PhaseResult(BaseModel):
    phase: str
    status: PipelineStatus
    duration_seconds: float = 0.0
    output_summary: Optional[str] = None
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class PipelineRun(BaseModel):
    run_id: str
    playlist_id: str
    started_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    phases: list[PhaseResult] = []
    overall_status: PipelineStatus = PipelineStatus.PENDING
    youtube_url: Optional[str] = None

    def get_phase(self, name: str) -> Optional[PhaseResult]:
        return next((p for p in self.phases if p.phase == name), None)

    def add_phase(self, result: PhaseResult) -> None:
        self.phases = [p for p in self.phases if p.phase != result.phase]
        self.phases.append(result)
