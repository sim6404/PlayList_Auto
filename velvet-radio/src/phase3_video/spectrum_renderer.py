"""
Velvet Radio — Phase 3: FFmpeg 오디오 스펙트럼 렌더링
배경 이미지 + 음원 → 곡별 시각화 영상 MP4 생성
"""
from __future__ import annotations

import subprocess
import shlex
from pathlib import Path

from ..common.config_loader import config
from ..common.logger import get_logger

logger = get_logger(__name__)

VIDEOS_DIR = config.data_dir / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# ── FFmpeg 필터 프리셋 ───────────────────────────────────────────

SPECTRUM_FILTERS = {
    # 클린 배경 — 파형 없이 순수 배경 이미지만 (기본값)
    "clean": (
        "[bg]null[vout]"
    ),
    # 주파수 막대 (선택적 사용)
    "freq": (
        "[1:a]showfreqs=s=1920x200:mode=bar:ascale=log:"
        "colors=0xE8D5B7@0.8,format=rgba[freq];"
        "[bg][freq]overlay=0:H-220:shortest=1[vout]"
    ),
    # 벡터스코프 (코너 소형)
    "vector": (
        "[1:a]avectorscope=s=400x400:zoom=1.5:draw=line:"
        "bc=0x000000:fc=0xFFFFFF@0.6,format=rgba[scope];"
        "[bg][scope]overlay=W-420:H-420:shortest=1[vout]"
    ),
}


def _build_ffmpeg_cmd(
    background_image: Path,
    audio_file: Path,
    output_path: Path,
    subtitle_file: Path | None = None,
    filter_type: str = "wave",
) -> list[str]:
    """FFmpeg 명령어 리스트 조립"""
    inputs = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(background_image),   # 0: background
        "-i", str(audio_file),                         # 1: audio
    ]

    spectrum_filter = SPECTRUM_FILTERS.get(filter_type, SPECTRUM_FILTERS["wave"])

    if subtitle_file and subtitle_file.exists():
        # 자막 포함
        filter_complex = (
            f"[0:v]scale=1920:1080,setsar=1[bg];"
            f"{spectrum_filter};"
            f"[vout]subtitles='{str(subtitle_file).replace(chr(92), '/')}'"
            f":force_style='FontName=Arial,FontSize=20,"
            f"PrimaryColour=&HFFFFFF,OutlineColour=&H000000,"
            f"Outline=1,Shadow=0,Alignment=2,MarginV=40'[final]"
        )
        map_out = "[final]"
    else:
        filter_complex = (
            f"[0:v]scale=1920:1080,setsar=1[bg];"
            f"{spectrum_filter}"
        )
        map_out = "[vout]"

    cmd = inputs + [
        "-filter_complex", filter_complex,
        "-map", map_out,
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-shortest",
        str(output_path),
    ]
    return cmd


def _run_ffmpeg(cmd: list[str], label: str) -> None:
    """
    FFmpeg 명령 실행 (bytes 모드) — Windows cp949 인코딩 충돌 방지.

    Raises:
        RuntimeError: returncode != 0
    """
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=False,   # bytes 모드 — Windows 인코딩 문제 방지
        timeout=300,
    )
    stderr_text = (result.stderr or b"").decode("utf-8", errors="replace")
    if result.returncode != 0:
        logger.error(
            "FFmpeg 실패",
            label=label,
            returncode=result.returncode,
            stderr_tail=stderr_text[-600:],
            cmd_head=" ".join(cmd[:8]),
        )
        raise RuntimeError(
            f"FFmpeg 렌더링 실패 [{label}] (rc={result.returncode}): {stderr_text[-300:]}"
        )
    logger.debug("FFmpeg 성공", label=label, returncode=result.returncode)


def render_track_video(
    background_image: Path,
    audio_file: Path,
    output_path: Path,
    subtitle_file: Path | None = None,
    filter_type: str = "clean",
) -> Path:
    """
    단일 트랙 영상 렌더링.
    자막 포함 렌더링이 실패하면 자막 없이 재시도하는 폴백 로직 포함.

    Returns:
        렌더링된 MP4 경로
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "FFmpeg 렌더링 시작",
        audio=audio_file.name,
        filter=filter_type,
        output=output_path.name,
        has_subtitle=subtitle_file is not None and (subtitle_file.exists() if subtitle_file else False),
    )

    # 1차 시도: 자막 포함 (subtitle_file이 있는 경우)
    cmd = _build_ffmpeg_cmd(background_image, audio_file, output_path, subtitle_file, filter_type)
    try:
        _run_ffmpeg(cmd, label=f"{audio_file.name}+subtitle")
    except RuntimeError as first_err:
        # 자막이 없었거나 자막 없이 이미 시도한 경우 — 바로 re-raise
        effective_subtitle = subtitle_file if (subtitle_file and subtitle_file.exists()) else None
        if effective_subtitle is None:
            raise

        # 2차 시도: 자막 필터 제거 후 재시도
        logger.warning(
            "자막 포함 렌더링 실패 — 자막 없이 재시도",
            audio=audio_file.name,
            first_error=str(first_err)[:200],
        )
        cmd_no_sub = _build_ffmpeg_cmd(background_image, audio_file, output_path, None, filter_type)
        _run_ffmpeg(cmd_no_sub, label=f"{audio_file.name}+no_subtitle_fallback")

    size_mb = output_path.stat().st_size / 1_048_576
    logger.info("FFmpeg 렌더링 완료", output=output_path.name, size_mb=f"{size_mb:.1f}MB")
    return output_path


def render_playlist_videos(
    playlist_id: str,
    track_assets: list[dict],  # [{order, title, audio_path, background_path, subtitle_path}]
    filter_type: str = "clean",
) -> list[dict]:
    """
    플레이리스트 전체 곡별 영상 렌더링

    Returns:
        [{order, title, video_path, success}]
    """
    out_dir = VIDEOS_DIR / playlist_id / "tracks"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for asset in sorted(track_assets, key=lambda x: x["order"]):
        order = asset["order"]
        out_path = out_dir / f"track_{order:02d}.mp4"
        try:
            render_track_video(
                background_image=Path(asset["background_path"]),
                audio_file=Path(asset["audio_path"]),
                output_path=out_path,
                subtitle_file=Path(asset["subtitle_path"]) if asset.get("subtitle_path") else None,
                filter_type=filter_type,
            )
            results.append({"order": order, "title": asset["title"], "video_path": str(out_path), "success": True})
        except Exception as e:
            logger.error("트랙 렌더링 실패", order=order, error=str(e))
            results.append({"order": order, "title": asset["title"], "video_path": None, "success": False})

    success = sum(1 for r in results if r["success"])
    logger.info("플레이리스트 렌더링 완료", playlist_id=playlist_id, success=success, total=len(results))
    return results
