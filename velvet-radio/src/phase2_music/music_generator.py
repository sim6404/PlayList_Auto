"""
Velvet Radio — Phase 2: 음악 생성 오케스트레이터
Phase 1 결과물 → Suno 배치 생성 → 품질 선별 → 완성 음원 목록 반환
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import Playlist, QualityReport, SunoPayload
from ..common.notifier import get_notifier
from ..phase1_concept.lyrics_generator import load_lyrics
from ..phase1_concept.style_assembler import build_all_payloads
from .quality_filter import select_best_variants
from .suno_client import SunoClient

logger = get_logger(__name__)


def _load_lyrics_map(playlist: Playlist) -> dict[int, str]:
    """저장된 가사 파일 전체 로드"""
    lyrics_map: dict[int, str] = {}
    for track in playlist.tracks:
        try:
            lyrics_map[track.order] = load_lyrics(playlist.id, track.order)
        except FileNotFoundError:
            logger.warning("가사 파일 없음, 인스트루멘탈 처리", track=track.order)
    return lyrics_map


async def run_music_generation(playlist: Playlist, progress_cb=None) -> QualityReport:
    """
    Phase 2 메인 진입점

    처리 순서:
    1. 가사 + 스타일 프롬프트 → SunoPayload 조립
    2. Suno API 배치 생성 (2변형 × 20곡)
    3. 품질 필터링 → 상위 20곡 선별
    4. QualityReport 반환

    Args:
        playlist: Phase 1에서 생성된 Playlist 객체

    Returns:
        QualityReport: 선별된 음원 정보 + 품질 리포트
    """
    notifier = get_notifier()
    client = SunoClient()

    # 1. SunoPayload 조립
    lyrics_map = _load_lyrics_map(playlist)
    payloads = build_all_payloads(playlist, lyrics_map)

    if not payloads:
        raise RuntimeError("SunoPayload가 비어있습니다")

    logger.info(
        "Phase 2 음악 생성 시작",
        playlist_id=playlist.id,
        payloads=len(payloads),
    )

    # 2. 배치 생성
    try:
        variant_paths = await client.generate_batch(
            payloads=payloads,
            playlist_id=playlist.id,
            variants=2,
            concurrency=config.max_concurrent_suno_jobs,
            progress_cb=progress_cb,
        )
    except Exception as e:
        logger.error("Suno 배치 생성 실패", error=str(e))
        notifier.notify_error("phase2", f"Suno 배치 생성 실패: {e}")
        raise

    # 3. 품질 선별
    track_titles = {t.order: t.title for t in playlist.tracks}
    report = select_best_variants(variant_paths, playlist.id, track_titles)

    # 품질 게이트: 최소 15곡
    if report.selected_count < config.min_tracks_for_publish:
        error_msg = (
            f"선별 곡 수 부족: {report.selected_count}곡 "
            f"(최소 {config.min_tracks_for_publish}곡 필요)"
        )
        notifier.notify_error("phase2", error_msg)
        raise RuntimeError(error_msg)

    summary = (
        f"✅ 선별 완료: {report.selected_count}/{len(playlist.tracks)}곡\n"
        f"❌ 실패 트랙: {report.failed_tracks or '없음'}\n"
        f"🎵 총 생성: {report.total_generated}개 변형"
    )
    notifier.notify_phase_complete("phase2", summary)
    logger.info("Phase 2 완료", **{
        "selected": report.selected_count,
        "failed": report.failed_tracks,
    })
    return report
