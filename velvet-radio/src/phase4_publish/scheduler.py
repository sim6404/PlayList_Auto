"""
Velvet Radio — Phase 4: 업로드 스케줄러 + 전체 파이프라인 오케스트레이터
마스터 승인 후 YouTube 업로드 자동 실행
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import (
    ApprovalStatus,
    PhaseResult,
    PipelineRun,
    PipelineStatus,
    UploadPrivacy,
    YouTubeUpload,
)
from ..common.notifier import get_notifier
from ..phase1_concept.lyrics_generator import generate_all_lyrics
from ..phase1_concept.theme_generator import generate_playlist
from ..phase2_music.music_generator import run_music_generation
from ..phase3_video.image_generator import ImageGeneratorClient
from ..phase3_video.playlist_concat import concat_playlist_videos
from ..phase3_video.spectrum_renderer import render_playlist_videos
from ..phase3_video.subtitle_generator import generate_srt_for_playlist
from ..phase4_publish.approval_manager import ApprovalManager
from ..phase4_publish.seo_generator import generate_seo_metadata
from ..phase4_publish.youtube_uploader import YouTubeUploader

logger = get_logger(__name__)


def _next_tuesday_or_friday_9am() -> str:
    """다음 화요일 또는 금요일 오전 9시 KST (ISO 8601 UTC)"""
    now = datetime.utcnow()
    # KST = UTC+9
    kst_now = now + timedelta(hours=9)
    target_weekdays = [1, 4]  # Tuesday=1, Friday=4

    for days_ahead in range(1, 8):
        candidate = kst_now + timedelta(days=days_ahead)
        if candidate.weekday() in target_weekdays:
            scheduled_kst = candidate.replace(hour=9, minute=0, second=0, microsecond=0)
            scheduled_utc = scheduled_kst - timedelta(hours=9)
            return scheduled_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    return ""


async def run_full_pipeline(
    playlist_id: Optional[str] = None,
    auto_schedule: bool = True,
) -> PipelineRun:
    """
    Velvet Radio 전체 파이프라인 실행

    Phase 1 → Phase 2 → Phase 3 → Phase 4 (승인 대기) → YouTube 업로드

    Args:
        playlist_id: 기존 플레이리스트 ID (None이면 새로 생성)
        auto_schedule: True이면 다음 화/금 9AM에 예약 발행

    Returns:
        PipelineRun: 전체 실행 결과
    """
    run_id = str(uuid.uuid4())[:8]
    notifier = get_notifier()
    approval_manager = ApprovalManager()

    run = PipelineRun(
        run_id=run_id,
        playlist_id=playlist_id or "",
        overall_status=PipelineStatus.RUNNING,
    )

    logger.info("═══ 파이프라인 시작 ═══", run_id=run_id)

    # ─── Phase 1: 컨셉 기획 + 가사 ───────────────────────────────

    phase1 = PhaseResult(phase="phase1", status=PipelineStatus.RUNNING)
    t0 = datetime.utcnow()
    try:
        playlist = generate_playlist()
        run.playlist_id = playlist.id
        notifier.notify_pipeline_start(playlist.id, playlist.theme)

        lyrics_list = generate_all_lyrics(playlist)

        phase1.status = PipelineStatus.COMPLETED
        phase1.output_summary = f"테마: {playlist.theme}, 가사: {len(lyrics_list)}/{len(playlist.tracks)}곡"
        notifier.notify_phase_complete("phase1", phase1.output_summary)

    except Exception as e:
        phase1.status = PipelineStatus.FAILED
        phase1.error = str(e)
        notifier.notify_error("phase1", str(e))
        run.overall_status = PipelineStatus.FAILED
        run.add_phase(phase1)
        return run

    finally:
        phase1.duration_seconds = (datetime.utcnow() - t0).total_seconds()
        run.add_phase(phase1)

    # ─── Phase 2: 음악 생성 ────────────────────────────────────────

    phase2 = PhaseResult(phase="phase2", status=PipelineStatus.RUNNING)
    t0 = datetime.utcnow()
    try:
        quality_report = await run_music_generation(playlist)
        phase2.status = PipelineStatus.COMPLETED
        phase2.output_summary = f"선별 곡: {quality_report.selected_count}곡"

    except Exception as e:
        phase2.status = PipelineStatus.FAILED
        phase2.error = str(e)
        notifier.notify_error("phase2", str(e))
        run.overall_status = PipelineStatus.FAILED
        run.add_phase(phase2)
        return run

    finally:
        phase2.duration_seconds = (datetime.utcnow() - t0).total_seconds()
        run.add_phase(phase2)

    # ─── Phase 3: 영상 제작 ────────────────────────────────────────

    phase3 = PhaseResult(phase="phase3", status=PipelineStatus.RUNNING)
    t0 = datetime.utcnow()
    try:
        img_client = ImageGeneratorClient()
        images = img_client.generate_for_playlist(playlist)

        srt_map = generate_srt_for_playlist(
            playlist.id,
            [{"order": a.track_order, "duration_seconds": a.duration_seconds}
             for a in quality_report.assets],
        )

        track_assets = [
            {
                "order": a.track_order,
                "title": a.title,
                "audio_path": a.file_path,
                "background_path": str(images["background"]),
                "subtitle_path": str(srt_map.get(a.track_order, "")),
            }
            for a in quality_report.assets if a.selected
        ]

        track_videos = render_playlist_videos(playlist.id, track_assets)
        valid_videos = [v for v in track_videos if v["success"]]

        video_asset = concat_playlist_videos(
            playlist=playlist,
            track_videos=valid_videos,
            thumbnail_path=images["thumbnail"],
            background_path=images["background"],
        )

        phase3.status = PipelineStatus.COMPLETED
        phase3.output_summary = (
            f"영상: {len(valid_videos)}트랙 합본, "
            f"길이: {video_asset.duration_seconds//60}분"
        )
        notifier.notify_phase_complete("phase3", phase3.output_summary)

    except Exception as e:
        phase3.status = PipelineStatus.FAILED
        phase3.error = str(e)
        notifier.notify_error("phase3", str(e))
        run.overall_status = PipelineStatus.FAILED
        run.add_phase(phase3)
        return run

    finally:
        phase3.duration_seconds = (datetime.utcnow() - t0).total_seconds()
        run.add_phase(phase3)

    # ─── Phase 4a: SEO 생성 ────────────────────────────────────────

    phase4a = PhaseResult(phase="phase4_seo", status=PipelineStatus.RUNNING)
    t0 = datetime.utcnow()
    try:
        seo = generate_seo_metadata(
            playlist=playlist,
            video_asset=video_asset,
            quality_report=quality_report,
        )
        phase4a.status = PipelineStatus.COMPLETED
        phase4a.output_summary = f"제목: {seo.title_en[:60]}"

    except Exception as e:
        phase4a.status = PipelineStatus.FAILED
        phase4a.error = str(e)

    finally:
        phase4a.duration_seconds = (datetime.utcnow() - t0).total_seconds()
        run.add_phase(phase4a)

    # ─── Phase 4b: 마스터 승인 요청 ────────────────────────────────

    approval_req = approval_manager.submit_for_approval(
        playlist_id=playlist.id,
        video_asset=video_asset,
        seo=seo,
        quality_report=quality_report,
    )

    notifier.notify_pipeline_complete(run, f"대시보드: http://localhost:{config.dashboard_port}/review/{playlist.id}")

    approval_status = approval_manager.wait_for_approval(playlist.id)

    # ─── Phase 4c: YouTube 업로드 (승인 후) ────────────────────────

    phase4c = PhaseResult(phase="phase4_upload", status=PipelineStatus.RUNNING)
    t0 = datetime.utcnow()

    if approval_status != ApprovalStatus.APPROVED:
        phase4c.status = PipelineStatus.SKIPPED
        phase4c.output_summary = f"업로드 건너뜀: {approval_status.value}"
        run.overall_status = PipelineStatus.COMPLETED
        run.add_phase(phase4c)
        logger.info("업로드 건너뜀", reason=approval_status.value)
        return run

    try:
        upload = YouTubeUpload(
            playlist_id=playlist.id,
            video_path=video_asset.final_video_path,
            title=seo.title_en,
            description=seo.description_en,
            tags=seo.tags,
            thumbnail_path=video_asset.thumbnail_path,
            privacy=UploadPrivacy.PRIVATE,
            scheduled_at=_next_tuesday_or_friday_9am() if auto_schedule else None,
        )

        uploader = YouTubeUploader()
        result = uploader.full_upload(upload, seo, video_asset)

        video_url = result["video_url"]
        run.youtube_url = video_url
        run.overall_status = PipelineStatus.COMPLETED

        phase4c.status = PipelineStatus.COMPLETED
        phase4c.output_summary = f"업로드 완료: {video_url}"

        notifier.notify_approved(playlist.id, video_url)
        logger.info("═══ 파이프라인 완료 ═══", url=video_url)

    except Exception as e:
        phase4c.status = PipelineStatus.FAILED
        phase4c.error = str(e)
        run.overall_status = PipelineStatus.FAILED
        notifier.notify_error("phase4_upload", str(e))

    finally:
        phase4c.duration_seconds = (datetime.utcnow() - t0).total_seconds()
        run.completed_at = datetime.utcnow().isoformat()
        run.add_phase(phase4c)

    return run
