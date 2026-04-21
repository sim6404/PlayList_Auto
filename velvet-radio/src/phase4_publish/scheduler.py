"""
Velvet Radio — Phase 4: 업로드 스케줄러 + 전체 파이프라인 오케스트레이터
마스터 승인 후 YouTube 업로드 자동 실행
"""
from __future__ import annotations

import asyncio
import json
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

SESSION_DIR = config.data_dir / "sessions"


# ─── 세션 로그 ────────────────────────────────────────────────────

def _slog(session_id: str, msg: str) -> None:
    """세션 활동 로그 파일에 한 줄 추가 (data/sessions/<id>.log)"""
    try:
        log_path = SESSION_DIR / f"{session_id}.log"
        ts = datetime.utcnow().strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ─── 세션 관리 ────────────────────────────────────────────────────

def _session_path(session_id: str) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR / f"{session_id}.json"


def load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"세션을 찾을 수 없습니다: {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(session_id: str, data: dict) -> None:
    data["updated_at"] = datetime.utcnow().isoformat()
    _session_path(session_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_sessions() -> list[dict]:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(SESSION_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            sessions.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return sessions


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

        lyrics_list = await generate_all_lyrics(playlist)

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


# ─── 단계별 실행 함수 (Pipeline Studio용) ────────────────────────────

async def run_phase1_only(session_id: str = None) -> dict:
    """Phase 1만 실행: 테마 + 가사 생성 → 세션에 저장"""
    session_id = session_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    notifier = get_notifier()
    t0 = datetime.utcnow()

    session: dict = {
        "session_id": session_id,
        "created_at": t0.isoformat(),
        "current_phase": 1,
        "phases": {"phase1": {"status": "running", "started_at": t0.isoformat()}},
    }
    save_session(session_id, session)
    _slog(session_id, "Phase 1 시작 — Claude AI 테마 기획 중...")

    try:
        playlist = generate_playlist()
        session["playlist_id"] = playlist.id
        _slog(session_id, f"테마 생성 완료: {playlist.theme} ({len(playlist.tracks)}트랙)")
        notifier.notify_pipeline_start(playlist.id, playlist.theme)

        _slog(session_id, f"가사 생성 시작 (총 {len(playlist.tracks)}곡, 병렬 5)...")
        lyrics_list = await generate_all_lyrics(playlist)
        _slog(session_id, f"가사 생성 완료: {len(lyrics_list)}/{len(playlist.tracks)}곡")

        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase1"] = {
            "status": "completed",
            "started_at": t0.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "duration_seconds": duration,
            "theme": playlist.theme,
            "playlist_id": playlist.id,
            "track_count": len(lyrics_list),
            "summary": f"테마: {playlist.theme}, 가사: {len(lyrics_list)}곡",
        }
        session["current_phase"] = 2
        save_session(session_id, session)
        notifier.notify_phase_complete("phase1", session["phases"]["phase1"]["summary"])
        _slog(session_id, f"Phase 1 완료 ({duration:.0f}초) — Phase 2 준비됨")
        logger.info("Phase 1 단독 완료", session_id=session_id, playlist_id=playlist.id)
        return session

    except Exception as e:
        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase1"] = {
            "status": "failed",
            "started_at": t0.isoformat(),
            "duration_seconds": duration,
            "error": str(e),
        }
        save_session(session_id, session)
        _slog(session_id, f"Phase 1 실패: {str(e)[:200]}")
        notifier.notify_error("phase1", str(e))
        raise


async def run_phase2_only(session_id: str) -> dict:
    """Phase 2만 실행: Suno 음악 생성"""
    from ..phase1_concept.theme_generator import load_playlist as _load_playlist

    session = load_session(session_id)
    playlist_id = session.get("playlist_id")
    if not playlist_id:
        raise ValueError("session에 playlist_id가 없습니다. Phase 1을 먼저 실행하세요.")

    playlist = _load_playlist(playlist_id)
    notifier = get_notifier()
    t0 = datetime.utcnow()
    total_tasks = len(playlist.tracks) * 2
    session.setdefault("phases", {})["phase2"] = {
        "status": "running",
        "started_at": t0.isoformat(),
        "total_tasks": total_tasks,
        "completed_tasks": 0,
    }
    save_session(session_id, session)
    _slog(session_id, f"Phase 2 시작 — Suno AI {total_tasks}개 작업 (20곡 × 2변형)")

    async def _on_progress(completed: int, total: int) -> None:
        session["phases"]["phase2"]["completed_tasks"] = completed
        session["phases"]["phase2"]["total_tasks"] = total
        save_session(session_id, session)
        _slog(session_id, f"Suno 진행: {completed}/{total} 완료")

    try:
        quality_report = await run_music_generation(playlist, progress_cb=_on_progress)
        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase2"] = {
            "status": "completed",
            "started_at": t0.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "duration_seconds": duration,
            "selected_count": quality_report.selected_count,
            "total_generated": quality_report.total_generated,
            "failed_tracks": quality_report.failed_tracks,
            "total_tasks": total_tasks,
            "completed_tasks": total_tasks,
            "summary": f"선별 곡: {quality_report.selected_count}곡",
            "quality_report_json": quality_report.model_dump_json(),
        }
        session["current_phase"] = 3
        save_session(session_id, session)
        _slog(session_id, f"Phase 2 완료 ({duration:.0f}초) — 선별: {quality_report.selected_count}곡")
        logger.info("Phase 2 단독 완료", session_id=session_id)
        return session

    except Exception as e:
        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase2"] = {
            **session["phases"].get("phase2", {}),
            "status": "failed",
            "duration_seconds": duration,
            "error": str(e),
        }
        save_session(session_id, session)
        _slog(session_id, f"Phase 2 실패: {str(e)[:200]}")
        notifier.notify_error("phase2", str(e))
        raise


async def run_phase3_only(session_id: str) -> dict:
    """Phase 3만 실행: 영상 제작"""
    from ..common.models import QualityReport
    from ..phase1_concept.theme_generator import load_playlist as _load_playlist

    session = load_session(session_id)
    playlist_id = session.get("playlist_id")
    phase2_data = session.get("phases", {}).get("phase2", {})
    quality_report_json = phase2_data.get("quality_report_json")
    if not quality_report_json:
        raise ValueError("Phase 2 결과가 없습니다. Phase 2를 먼저 실행하세요.")

    playlist = _load_playlist(playlist_id)
    quality_report = QualityReport.model_validate_json(quality_report_json)
    notifier = get_notifier()
    t0 = datetime.utcnow()
    session.setdefault("phases", {})["phase3"] = {
        "status": "running", "started_at": t0.isoformat()
    }
    save_session(session_id, session)
    _slog(session_id, "Phase 3 시작 — 이미지 생성 + 영상 렌더링 중...")

    try:
        _slog(session_id, "이미지 생성 중 (배경 + 썸네일)...")
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

        _slog(session_id, f"영상 렌더링 중 ({len(track_assets)}트랙)...")
        track_videos = render_playlist_videos(playlist.id, track_assets)
        valid_videos = [v for v in track_videos if v["success"]]
        _slog(session_id, f"렌더링 완료: {len(valid_videos)}/{len(track_assets)}트랙 성공.")

        if len(valid_videos) == 0:
            failed_titles = [v.get("title", f"track_{v['order']}") for v in track_videos if not v["success"]]
            raise RuntimeError(
                f"유효한 트랙 영상이 없습니다 — {len(track_assets)}개 모두 렌더링 실패. "
                f"실패 트랙: {', '.join(str(t) for t in failed_titles[:5])}"
            )

        _slog(session_id, f"{len(valid_videos)}트랙 합본 중...")
        video_asset = concat_playlist_videos(
            playlist=playlist,
            track_videos=valid_videos,
            thumbnail_path=images["thumbnail"],
            background_path=images["background"],
        )

        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase3"] = {
            "status": "completed",
            "started_at": t0.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "duration_seconds": duration,
            "video_count": len(valid_videos),
            "video_duration": video_asset.duration_seconds,
            "thumbnail_path": video_asset.thumbnail_path,
            "final_video_path": video_asset.final_video_path,
            "summary": f"영상: {len(valid_videos)}트랙 합본, {video_asset.duration_seconds // 60}분",
            "video_asset_json": video_asset.model_dump_json(),
        }
        session["current_phase"] = 4
        save_session(session_id, session)
        notifier.notify_phase_complete("phase3", session["phases"]["phase3"]["summary"])
        _slog(session_id, f"Phase 3 완료 ({duration:.0f}초) — {len(valid_videos)}트랙 합본 영상 생성됨")
        logger.info("Phase 3 단독 완료", session_id=session_id)
        return session

    except Exception as e:
        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase3"] = {
            **session["phases"].get("phase3", {}),
            "status": "failed",
            "duration_seconds": duration,
            "error": str(e),
        }
        save_session(session_id, session)
        _slog(session_id, f"Phase 3 실패: {str(e)[:200]}")
        notifier.notify_error("phase3", str(e))
        raise


async def run_phase4_only(session_id: str) -> dict:
    """Phase 4만 실행: SEO + 마스터 승인 + YouTube 업로드"""
    from ..common.models import QualityReport, VideoAsset
    from ..phase1_concept.theme_generator import load_playlist as _load_playlist

    session = load_session(session_id)
    playlist_id = session.get("playlist_id")
    phase2_data = session.get("phases", {}).get("phase2", {})
    phase3_data = session.get("phases", {}).get("phase3", {})
    quality_report_json = phase2_data.get("quality_report_json")
    video_asset_json = phase3_data.get("video_asset_json")
    if not video_asset_json:
        raise ValueError("Phase 3 결과가 없습니다. Phase 3를 먼저 실행하세요.")

    playlist = _load_playlist(playlist_id)
    quality_report = QualityReport.model_validate_json(quality_report_json)
    video_asset = VideoAsset.model_validate_json(video_asset_json)
    notifier = get_notifier()
    approval_manager = ApprovalManager()
    t0 = datetime.utcnow()
    session.setdefault("phases", {})["phase4"] = {
        "status": "running", "started_at": t0.isoformat()
    }
    save_session(session_id, session)
    _slog(session_id, "Phase 4 시작 — SEO 메타데이터 생성 중...")

    try:
        seo = generate_seo_metadata(
            playlist=playlist,
            video_asset=video_asset,
            quality_report=quality_report,
        )
        _slog(session_id, f"SEO 생성 완료: {seo.title_en[:60]}")

        approval_manager.submit_for_approval(
            playlist_id=playlist.id,
            video_asset=video_asset,
            seo=seo,
            quality_report=quality_report,
        )

        dummy_run = PipelineRun(
            run_id=session_id[:8],
            playlist_id=playlist_id,
            overall_status=PipelineStatus.RUNNING,
        )
        notifier.notify_pipeline_complete(
            dummy_run,
            f"대시보드: http://localhost:{config.dashboard_port}/review/{playlist.id}",
        )
        _slog(session_id, "마스터 승인 대기 중... (Telegram 또는 대시보드에서 승인하세요)")

        approval_status = approval_manager.wait_for_approval(playlist.id)
        _slog(session_id, f"승인 결과: {approval_status.value}")

        if approval_status != ApprovalStatus.APPROVED:
            duration = (datetime.utcnow() - t0).total_seconds()
            session["phases"]["phase4"] = {
                "status": "skipped",
                "duration_seconds": duration,
                "summary": f"업로드 건너뜀: {approval_status.value}",
            }
            save_session(session_id, session)
            _slog(session_id, f"Phase 4 종료 — 업로드 건너뜀 ({approval_status.value})")
            return session

        upload = YouTubeUpload(
            playlist_id=playlist.id,
            video_path=video_asset.final_video_path,
            title=seo.title_en,
            description=seo.description_en,
            tags=seo.tags,
            thumbnail_path=video_asset.thumbnail_path,
            privacy=UploadPrivacy.PRIVATE,
            scheduled_at=_next_tuesday_or_friday_9am(),
        )

        _slog(session_id, "YouTube 업로드 중...")
        uploader = YouTubeUploader()
        result = uploader.full_upload(upload, seo, video_asset)
        video_url = result["video_url"]

        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase4"] = {
            "status": "completed",
            "started_at": t0.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "duration_seconds": duration,
            "youtube_url": video_url,
            "seo_title": seo.title_en,
            "summary": f"YouTube 업로드 완료: {video_url}",
        }
        save_session(session_id, session)
        notifier.notify_approved(playlist.id, video_url)
        _slog(session_id, f"Phase 4 완료 ({duration:.0f}초) — YouTube: {video_url}")
        logger.info("Phase 4 단독 완료", session_id=session_id, url=video_url)
        return session

    except Exception as e:
        duration = (datetime.utcnow() - t0).total_seconds()
        session["phases"]["phase4"] = {
            **session["phases"].get("phase4", {}),
            "status": "failed",
            "duration_seconds": duration,
            "error": str(e),
        }
        save_session(session_id, session)
        _slog(session_id, f"Phase 4 실패: {str(e)[:200]}")
        notifier.notify_error("phase4", str(e))
        raise
