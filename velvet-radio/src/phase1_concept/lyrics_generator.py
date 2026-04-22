"""
Velvet Radio — Phase 1: 곡별 가사 생성
Claude API → Suno 메타태그 포함 가사 → data/lyrics/ 저장
"""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..common.claude_client import get_claude_client
from ..common.config_loader import config, load_prompt
from ..common.logger import get_logger
from ..common.models import Lyrics, Playlist, Track

logger = get_logger(__name__)

LYRICS_DIR = config.data_dir / "lyrics"
LYRICS_DIR.mkdir(parents=True, exist_ok=True)


def _validate_and_fix(content: str, track: Track) -> str:
    """
    가사 유효성 검사 + 자동 수정
    - 3,000자 초과 시 Bridge 축소
    - 필수 메타태그 누락 시 추가
    """
    # 필수 태그 확인
    if "[Verse" not in content:
        content = "[Verse]\n" + content
    if "[Chorus]" not in content:
        content += "\n\n[Chorus]\nOh, this moment feels like home\nA gentle place I've always known"

    # 3,000자 제한
    if len(content) > 3000:
        # Bridge 섹션 축소 시도
        bridge_match = re.search(r"\[Bridge\](.*?)(?=\[|$)", content, re.DOTALL)
        if bridge_match:
            bridge_text = bridge_match.group(1).strip()
            lines = [l for l in bridge_text.split("\n") if l.strip()]
            shortened = "\n".join(lines[:4])
            content = content.replace(bridge_match.group(0), f"[Bridge]\n{shortened}\n\n")

        # 여전히 초과 시 Outro 축소
        if len(content) > 3000:
            content = content[:2950].rsplit("\n", 1)[0]
            content += "\n\n[Outro]\n[Soft] Mmm..."

        logger.warning(
            "가사 3,000자 초과로 축소됨",
            track=track.order,
            final_length=len(content),
        )

    return content.strip()


def generate_lyrics_for_track(track: Track) -> Lyrics:
    """단일 트랙 가사 생성"""
    client = get_claude_client()
    system_prompt = load_prompt("system_lyrics")

    track_info = {
        "title": track.title,
        "mood": track.mood.value,
        "sub_genre": track.sub_genre,
        "bpm": track.bpm,
        "key": track.key,
        "vocal": track.vocal.value,
        "hook_priority": track.hook_priority,
        "concept_note": track.concept_note,
    }

    raw_content = client.generate_lyrics(system_prompt, track_info)
    content = _validate_and_fix(raw_content, track)

    return Lyrics(
        track_order=track.order,
        content=content,
        language="en",
    )


async def generate_all_lyrics(playlist: Playlist, max_concurrent: int = 5) -> list[Lyrics]:
    """
    플레이리스트 전체 20곡 가사 병렬 생성 (순차 → 동시 실행으로 개선)

    ThreadPoolExecutor로 동기 Claude API 호출을 병렬화하여
    20곡 × ~9s → 약 40~50s (기존 ~180s 대비 ~4배 속도)

    Returns:
        list[Lyrics]: 생성된 가사 목록 (data/lyrics/{playlist_id}/ 에 저장)
    """
    out_dir = LYRICS_DIR / playlist.id
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("가사 생성 시작 (병렬)", playlist_id=playlist.id, total=len(playlist.tracks), concurrency=max_concurrent)

    semaphore = asyncio.Semaphore(max_concurrent)
    loop = asyncio.get_running_loop()   # get_event_loop() → 실행 중 루프와 불일치 버그 수정
    executor = ThreadPoolExecutor(max_workers=max_concurrent)

    async def _gen_one(track: Track) -> tuple[Lyrics | None, int]:
        async with semaphore:
            try:
                lyrics = await loop.run_in_executor(executor, generate_lyrics_for_track, track)
                out_path = out_dir / f"track_{track.order:02d}.txt"
                out_path.write_text(lyrics.content, encoding="utf-8")
                logger.info("가사 생성 완료", order=track.order, title=track.title, chars=lyrics.char_count)
                return lyrics, track.order
            except Exception as e:
                logger.error("가사 생성 실패", order=track.order, title=track.title, error=str(e))
                return None, track.order

    results = await asyncio.gather(*[_gen_one(t) for t in playlist.tracks])
    executor.shutdown(wait=False)

    all_lyrics = [lyr for lyr, _ in results if lyr is not None]
    failed = [order for lyr, order in results if lyr is None]

    logger.info(
        "전체 가사 생성 완료",
        playlist_id=playlist.id,
        success=len(all_lyrics),
        failed=failed,
    )
    return all_lyrics


def load_lyrics(playlist_id: str, track_order: int) -> str:
    """저장된 가사 로드"""
    path = LYRICS_DIR / playlist_id / f"track_{track_order:02d}.txt"
    if not path.exists():
        raise FileNotFoundError(f"가사 파일 없음: {path}")
    return path.read_text(encoding="utf-8")
