"""
Velvet Radio — Phase 3: 플레이리스트 영상 합본
20개 트랙 영상 → FFmpeg concat → 챕터 메타데이터 삽입 → 최종 1시간+ MP4
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import Playlist, VideoAsset

logger = get_logger(__name__)

VIDEOS_DIR = config.data_dir / "videos"


def _get_video_duration(video_path: Path) -> float:
    """ffprobe로 영상 길이 추출"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_entries", "format=duration",
            str(video_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    return 0.0


def _generate_chapters_metadata(
    track_items: list[dict],  # [{order, title, video_path}]
) -> str:
    """
    FFmpeg chapters 메타데이터 텍스트 생성

    형식:
    ;FFMETADATA1
    [CHAPTER]
    TIMEBASE=1/1000
    START=0
    END=180000
    title=01. Track Title
    """
    lines = [";FFMETADATA1\n"]
    current_ms = 0

    for item in track_items:
        path = Path(item["video_path"])
        if not path.exists():
            continue

        duration_s = _get_video_duration(path)
        duration_ms = int(duration_s * 1000)
        end_ms = current_ms + duration_ms

        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={current_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={item['order']:02d}. {item['title']}")
        lines.append("")

        current_ms = end_ms

    return "\n".join(lines)


def _generate_chapter_timestamps(
    track_items: list[dict],
) -> list[str]:
    """YouTube 설명란 챕터 타임스탬프 텍스트 생성"""
    timestamps: list[str] = ["0:00 Intro"]
    current_s = 0.0

    for item in track_items:
        path = Path(item["video_path"])
        if not path.exists():
            continue
        duration = _get_video_duration(path)
        h = int(current_s) // 3600
        m = int(current_s) // 60 % 60
        s = int(current_s) % 60
        ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        timestamps.append(f"{ts} {item['order']:02d}. {item['title']}")
        current_s += duration

    return timestamps


def concat_playlist_videos(
    playlist: Playlist,
    track_videos: list[dict],   # [{order, title, video_path}]
    thumbnail_path: Path,
    background_path: Path,
) -> VideoAsset:
    """
    플레이리스트 최종 영상 합본

    처리 순서:
    1. filelist.txt 생성
    2. FFmpeg concat (stream copy)
    3. 챕터 메타데이터 삽입
    4. 총 길이 검증

    Returns:
        VideoAsset: 완성된 영상 정보
    """
    valid_videos = [v for v in track_videos if v.get("video_path") and Path(v["video_path"]).exists()]
    if not valid_videos:
        raise RuntimeError("합본할 유효한 트랙 영상이 없습니다")

    out_dir = VIDEOS_DIR / playlist.id
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"{playlist.id}_final.mp4"

    # 1. filelist.txt 작성 — 절대경로 사용 (상대경로는 %TEMP% 기준으로 잘못 해석됨)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for v in sorted(valid_videos, key=lambda x: x["order"]):
            abs_path = str(Path(v["video_path"]).resolve()).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")
        filelist_path = f.name

    # 2. FFmpeg concat
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", filelist_path,
        "-c", "copy",
        str(final_path),
    ]
    logger.info("영상 합본 시작", tracks=len(valid_videos))
    result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat 실패: {result.stderr[-300:]}")

    # 3. 챕터 메타데이터 삽입
    chapters_meta = _generate_chapters_metadata(valid_videos)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(chapters_meta)
        meta_path = f.name

    chaptered_path = out_dir / f"{playlist.id}_chaptered.mp4"
    chapter_cmd = [
        "ffmpeg", "-y",
        "-i", str(final_path),
        "-i", meta_path,
        "-map_metadata", "1",
        "-codec", "copy",
        str(chaptered_path),
    ]
    result = subprocess.run(chapter_cmd, capture_output=True, text=True, timeout=600)
    if result.returncode == 0:
        final_path.unlink()
        chaptered_path.rename(final_path)
        logger.info("챕터 삽입 완료")
    else:
        logger.warning("챕터 삽입 실패, 챕터 없는 버전 사용", stderr=result.stderr[-200:])

    # 4. 총 길이 검증
    total_duration = _get_video_duration(final_path)
    logger.info(
        "최종 영상 완성",
        path=final_path.name,
        duration_min=f"{total_duration/60:.1f}분",
        size_mb=f"{final_path.stat().st_size/1_048_576:.0f}MB",
    )

    # 챕터 타임스탬프 (YouTube 설명란용)
    chapter_timestamps = _generate_chapter_timestamps(valid_videos)

    # SRT 경로 (첫 번째 트랙의 것 참조)
    srt_ref = ""
    if valid_videos:
        srt_path = VIDEOS_DIR / playlist.id / "subtitles" / f"track_01.srt"
        srt_ref = str(srt_path) if srt_path.exists() else ""

    return VideoAsset(
        playlist_id=playlist.id,
        thumbnail_path=str(thumbnail_path),
        background_path=str(background_path),
        spectrum_video_path="",   # 개별 트랙 영상들
        subtitle_path=srt_ref,
        final_video_path=str(final_path),
        duration_seconds=int(total_duration),
        has_chapters=True,
    )
