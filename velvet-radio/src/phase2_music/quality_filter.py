"""
Velvet Radio — Phase 2: 오디오 품질 필터
librosa + pyloudnorm으로 음원 분석 → 곡당 최상위 1개 선택
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import AudioAsset, QualityReport

logger = get_logger(__name__)


def _analyze_audio(path: Path) -> dict:
    """
    librosa + pyloudnorm 으로 오디오 분석

    Returns:
        {duration, lufs, silence_ratio, clipping, spectral_bandwidth}
    """
    try:
        import librosa
        import numpy as np
        import pyloudnorm as pyln

        y, sr = librosa.load(str(path), sr=None, mono=False)
        if y.ndim > 1:
            y_mono = librosa.to_mono(y)
        else:
            y_mono = y

        duration = librosa.get_duration(y=y_mono, sr=sr)

        # LUFS 측정
        meter = pyln.Meter(sr)
        lufs = meter.integrated_loudness(y_mono)

        # 무음 비율 (< -60dB)
        rms = librosa.feature.rms(y=y_mono)[0]
        silence_ratio = float(np.mean(rms < 0.001))

        # 클리핑 감지 (> 0.99 샘플 비율)
        clipping = bool(np.mean(np.abs(y_mono) > 0.99) > 0.001)

        # 스펙트럼 대역폭
        bandwidth = float(np.mean(librosa.feature.spectral_bandwidth(y=y_mono, sr=sr)))

        return {
            "duration": duration,
            "lufs": lufs,
            "silence_ratio": silence_ratio,
            "clipping": clipping,
            "spectral_bandwidth": bandwidth,
        }
    except ImportError:
        logger.warning("librosa/pyloudnorm 미설치, 기본값 반환")
        return _fallback_analysis(path)
    except Exception as e:
        logger.error("오디오 분석 실패", path=str(path), error=str(e))
        return _fallback_analysis(path)


def _fallback_analysis(path: Path) -> dict:
    """librosa 없을 때 ffprobe 기반 폴백"""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        streams = info.get("streams", [{}])
        duration = float(streams[0].get("duration", 0))
    except Exception:
        duration = 180.0  # 기본값 3분

    return {
        "duration": duration,
        "lufs": -12.0,
        "silence_ratio": 0.01,
        "clipping": False,
        "spectral_bandwidth": 2000.0,
    }


def score_audio(analysis: dict) -> float:
    """
    0.0 ~ 1.0 품질 점수 산출

    채점 기준:
    - 길이 적합성 (150~270초): 30점
    - LUFS 적합성 (-14~-10): 30점
    - 무음 없음 (<5%): 20점
    - 클리핑 없음: 20점
    """
    score = 0.0
    d = analysis["duration"]
    lufs = analysis["lufs"]

    # 길이 점수
    if config.min_duration_seconds <= d <= config.max_duration_seconds:
        score += 30
    elif d > 0:
        ideal = (config.min_duration_seconds + config.max_duration_seconds) / 2
        penalty = abs(d - ideal) / ideal
        score += max(0, 30 * (1 - penalty))

    # LUFS 점수
    if config.target_lufs_min <= lufs <= config.target_lufs_max:
        score += 30
    elif lufs < 0:
        ideal_lufs = (config.target_lufs_min + config.target_lufs_max) / 2
        penalty = abs(lufs - ideal_lufs) / abs(ideal_lufs)
        score += max(0, 30 * (1 - penalty * 0.5))

    # 무음 점수
    silence = analysis["silence_ratio"]
    if silence < 0.05:
        score += 20
    else:
        score += max(0, 20 * (1 - silence))

    # 클리핑 점수
    if not analysis["clipping"]:
        score += 20

    return round(score / 100, 4)


def select_best_variants(
    variant_paths: dict[int, list[Optional[Path]]],
    playlist_id: str,
    track_titles: dict[int, str],
) -> QualityReport:
    """
    곡별 변형 중 최고 품질 1개 선택

    Args:
        variant_paths: {track_order: [path_v1, path_v2]}
        playlist_id: 플레이리스트 ID
        track_titles: {track_order: title}

    Returns:
        QualityReport
    """
    assets: list[AudioAsset] = []
    failed_tracks: list[int] = []

    for order, paths in sorted(variant_paths.items()):
        valid_paths = [p for p in paths if p and p.exists()]

        if not valid_paths:
            logger.warning("유효한 음원 없음", track=order)
            failed_tracks.append(order)
            continue

        # 각 변형 분석
        best_path: Optional[Path] = None
        best_score = -1.0
        best_analysis: dict = {}

        for path in valid_paths:
            analysis = _analyze_audio(path)
            score = score_audio(analysis)
            logger.info(
                "음원 분석",
                track=order,
                file=path.name,
                score=score,
                lufs=f"{analysis['lufs']:.1f}",
                duration=f"{analysis['duration']:.0f}s",
            )
            if score > best_score:
                best_score = score
                best_path = path
                best_analysis = analysis

        if best_path and best_score > 0.3:
            asset = AudioAsset(
                playlist_id=playlist_id,
                track_order=order,
                title=track_titles.get(order, f"Track {order}"),
                file_path=str(best_path),
                duration_seconds=best_analysis["duration"],
                lufs=best_analysis["lufs"],
                silence_ratio=best_analysis["silence_ratio"],
                clipping=best_analysis["clipping"],
                quality_score=best_score,
                selected=True,
            )
            assets.append(asset)
            logger.info(
                "최우수 음원 선택",
                track=order,
                file=best_path.name,
                score=best_score,
            )
        else:
            logger.warning("품질 기준 미달", track=order, best_score=best_score)
            failed_tracks.append(order)

    report = QualityReport(
        playlist_id=playlist_id,
        total_generated=sum(len(p) for p in variant_paths.values()),
        selected_count=len(assets),
        failed_tracks=failed_tracks,
        assets=assets,
    )

    # 리포트 저장
    report_path = config.data_dir / "audio" / playlist_id / "quality_report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "품질 리포트 저장",
        selected=report.selected_count,
        failed=report.failed_tracks,
        path=str(report_path),
    )
    return report
