"""
Velvet Radio — Phase 3: SRT 자막 생성
가사 텍스트 → 타임스탬프 동기화 SRT 파일 생성
"""
from __future__ import annotations

import re
from pathlib import Path

from ..common.config_loader import config
from ..common.logger import get_logger

logger = get_logger(__name__)

LYRICS_DIR = config.data_dir / "lyrics"


def _strip_metatags(text: str) -> list[str]:
    """Suno 메타태그 제거 후 가사 줄만 추출"""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # 메타태그 제거 (대괄호 전체)
        if re.match(r"^\[.*\]$", stripped):
            continue
        # 인라인 성능 태그 제거
        cleaned = re.sub(r"\[(Soft|Breathy|Whispered|Belted|Gentle|Airy|Warm|Spoken Word)\]", "", stripped).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _seconds_to_srt_time(seconds: float) -> str:
    """초 → SRT 타임스탬프 형식 (HH:MM:SS,mmm)"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(
    lyrics_content: str,
    audio_duration: float,
    output_path: Path,
    chars_per_second: float = 10.0,
) -> Path:
    """
    가사 → SRT 자막 생성 (타이밍 추정)

    타이밍 방식: 전체 가사 문자 수 기반 비례 배분
    (Whisper ASR을 사용하면 더 정확하지만 비용 발생)

    Args:
        lyrics_content: Suno 메타태그 포함 원본 가사
        audio_duration: 음원 길이 (초)
        output_path: SRT 파일 저장 경로
        chars_per_second: 가사 읽기 속도 추정

    Returns:
        저장된 SRT 파일 경로
    """
    lines = _strip_metatags(lyrics_content)
    if not lines:
        logger.warning("자막 생성 불가: 가사 없음", path=str(output_path))
        output_path.write_text("", encoding="utf-8")
        return output_path

    total_chars = sum(len(l) for l in lines)
    srt_entries: list[str] = []
    current_time = 2.0  # 인트로 2초 여백

    for idx, line in enumerate(lines, start=1):
        char_ratio = len(line) / max(total_chars, 1)
        # 가사 표시 시간: 전체 길이에서 비례 배분, 최소 2초, 최대 8초
        display_duration = max(2.0, min(8.0, audio_duration * char_ratio * 2.5))
        end_time = min(current_time + display_duration, audio_duration - 0.5)

        srt_entries.append(
            f"{idx}\n"
            f"{_seconds_to_srt_time(current_time)} --> {_seconds_to_srt_time(end_time)}\n"
            f"{line}\n"
        )
        current_time = end_time + 0.3  # 0.3초 간격

        if current_time >= audio_duration - 1.0:
            break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(srt_entries), encoding="utf-8")
    logger.info("SRT 자막 생성 완료", lines=len(srt_entries), path=output_path.name)
    return output_path


def generate_srt_for_playlist(
    playlist_id: str,
    track_data: list[dict],  # [{order, duration_seconds}]
) -> dict[int, Path]:
    """
    플레이리스트 전체 SRT 생성

    Returns:
        {track_order: srt_path}
    """
    srt_dir = config.data_dir / "videos" / playlist_id / "subtitles"
    srt_dir.mkdir(parents=True, exist_ok=True)

    result: dict[int, Path] = {}
    for item in track_data:
        order = item["order"]
        lyrics_path = LYRICS_DIR / playlist_id / f"track_{order:02d}.txt"
        if not lyrics_path.exists():
            continue

        lyrics = lyrics_path.read_text(encoding="utf-8")
        srt_path = srt_dir / f"track_{order:02d}.srt"
        generate_srt(
            lyrics_content=lyrics,
            audio_duration=item.get("duration_seconds", 180.0),
            output_path=srt_path,
        )
        result[order] = srt_path

    return result
