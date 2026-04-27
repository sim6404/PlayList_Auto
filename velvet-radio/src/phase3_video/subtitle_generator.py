"""
Velvet Radio — Phase 3: SRT 자막 생성 (v2 — 섹션 인식 + BPM 기반 싱크)

v2 개선 사항:
1. 섹션 인식 타이밍: [Verse] / [Chorus] / [Bridge] / [Outro] 별 시간 배분
2. BPM 기반 줄 단위 최소 시간 (2박자 최소)
3. [Instrumental] / [Interlude] 구간 자막 표시 건너뜀
4. 섹션 전환 시 자연스러운 공백(0.5초) 삽입
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..common.config_loader import config
from ..common.logger import get_logger

logger = get_logger(__name__)

LYRICS_DIR = config.data_dir / "lyrics"

# ── 섹션 가중치 (시간 배분 비율) ───────────────────────────────────────
# Chorus: 청중이 집중하므로 더 여유롭게
# Verse: 기본 속도
# Bridge: 감정 강조를 위해 약간 느리게
# Outro: 마무리, 천천히

SECTION_WEIGHTS: dict[str, float] = {
    "verse":          1.0,
    "pre-chorus":     0.85,
    "chorus":         1.25,
    "post-chorus":    0.9,
    "bridge":         1.15,
    "outro":          1.3,
    "hook":           1.1,
    "refrain":        1.2,
    # 아래 섹션은 가사 없음 → 자막 표시 건너뜀
    "instrumental":   0.0,
    "interlude":      0.0,
    "intro":          0.0,
    "break":          0.0,
    "solo":           0.0,
    "ad-lib":         0.0,
}

# 섹션 전환 사이 공백 (초)
SECTION_GAP = 0.5
# 인트로 여백 (초)
INTRO_PAD = 2.0
# 아웃트로 여백 (초)
OUTRO_PAD = 1.5
# 줄 사이 최소 갭 (초)
LINE_GAP = 0.2

# 최소/최대 줄 표시 시간 (초)
MIN_LINE_DURATION = 1.5
MAX_LINE_DURATION = 10.0


def _normalize_section_name(tag: str) -> str:
    """[Verse 1] → verse, [Pre-Chorus] → pre-chorus 등 정규화"""
    inner = tag.strip("[]").lower().strip()
    # 숫자/콜론 제거
    inner = re.sub(r"[\d:]+", "", inner).strip()
    # 다양한 표기 → 표준화
    aliases = {
        "verse":        "verse",
        "pre chorus":   "pre-chorus",
        "prechorus":    "pre-chorus",
        "chorus":       "chorus",
        "post chorus":  "post-chorus",
        "postchorus":   "post-chorus",
        "bridge":       "bridge",
        "outro":        "outro",
        "hook":         "hook",
        "refrain":      "refrain",
        "instrumental": "instrumental",
        "interlude":    "interlude",
        "intro":        "intro",
        "break":        "break",
        "solo":         "solo",
        "ad lib":       "ad-lib",
        "ad-lib":       "ad-lib",
        "adlib":        "ad-lib",
    }
    for key, val in aliases.items():
        if key in inner:
            return val
    return inner


def _is_section_tag(line: str) -> Optional[str]:
    """줄이 섹션 태그이면 정규화된 섹션명 반환, 아니면 None"""
    stripped = line.strip()
    if re.match(r"^\[.*\]$", stripped):
        return _normalize_section_name(stripped)
    return None


def _strip_performance_tags(line: str) -> str:
    """인라인 퍼포먼스 태그 제거: [Soft], [Breathy] 등"""
    return re.sub(
        r"\[(Soft|Breathy|Whispered|Belted|Gentle|Airy|Warm|Spoken Word|Falsetto)\]",
        "",
        line,
    ).strip()


def _parse_sections(lyrics: str) -> list[tuple[str, list[str]]]:
    """
    가사를 섹션별로 파싱

    Returns:
        [(section_name, [lyric_lines]), ...]
    """
    sections: list[tuple[str, list[str]]] = []
    current_section = "verse"  # 태그 없이 시작할 경우 기본값
    current_lines: list[str] = []

    for raw_line in lyrics.splitlines():
        section_name = _is_section_tag(raw_line)
        if section_name is not None:
            # 현재 섹션 저장
            if current_lines:
                sections.append((current_section, current_lines))
            current_section = section_name
            current_lines = []
        else:
            cleaned = _strip_performance_tags(raw_line)
            if cleaned:
                current_lines.append(cleaned)

    if current_lines:
        sections.append((current_section, current_lines))

    return sections


def _estimate_syllables(line: str) -> float:
    """
    영어 기준 음절 수 추정
    (단어당 평균 1.5음절 × 단어 수, 최소 1)
    """
    words = line.split()
    if not words:
        return 1.0
    # 짧은 단어(1~2자) = 1음절, 긴 단어 = 2~3음절
    syllables = sum(
        1 if len(w) <= 2 else
        2 if len(w) <= 5 else
        3
        for w in words
    )
    return max(1.0, float(syllables))


def _beat_duration(bpm: float) -> float:
    """BPM → 1박자 길이 (초)"""
    return 60.0 / max(bpm, 40.0)


def _seconds_to_srt_time(seconds: float) -> str:
    """초 → SRT 타임스탬프 (HH:MM:SS,mmm)"""
    seconds = max(0.0, seconds)
    ms = int(round((seconds % 1) * 1000))
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(
    lyrics_content: str,
    audio_duration: float,
    output_path: Path,
    bpm: float = 90.0,
) -> Path:
    """
    v2 — 섹션 인식 + BPM 기반 자막 싱크 알고리즘

    알고리즘:
    1. 섹션별 파싱 → 악기 구간 제외
    2. 유효 섹션의 "가중 음절 수" 계산
    3. 가용 시간을 가중치에 따라 배분
    4. 각 섹션 내 줄별로 음절 비례 배분
    5. BPM 기반 최소 줄 시간 보장 (2박자)

    Args:
        lyrics_content: Suno 메타태그 포함 원본 가사
        audio_duration: 음원 길이 (초)
        output_path: SRT 저장 경로
        bpm: 트랙 BPM (기본 90)
    """
    sections = _parse_sections(lyrics_content)
    if not sections:
        logger.warning("자막 생성 불가: 가사 없음", path=str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        return output_path

    beat = _beat_duration(bpm)
    min_line_dur = max(MIN_LINE_DURATION, beat * 2)   # 최소 2박자

    # 유효 섹션 (악기 구간 제외)
    lyric_sections = [
        (name, lines)
        for name, lines in sections
        if SECTION_WEIGHTS.get(name, 1.0) > 0 and lines
    ]

    if not lyric_sections:
        logger.warning("자막 가사 줄 없음", path=str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        return output_path

    # 총 가용 시간 (인트로/아웃트로 패딩 제외)
    total_lyric_time = max(
        10.0,
        audio_duration - INTRO_PAD - OUTRO_PAD
        - SECTION_GAP * max(0, len(lyric_sections) - 1),
    )

    # 섹션별 가중 음절 수 계산
    section_syllables: list[tuple[str, list[str], float]] = []
    total_weighted_syllables = 0.0

    for name, lines in lyric_sections:
        weight = SECTION_WEIGHTS.get(name, 1.0)
        raw_syllables = sum(_estimate_syllables(l) for l in lines)
        weighted = raw_syllables * weight
        section_syllables.append((name, lines, weighted))
        total_weighted_syllables += weighted

    # 섹션별 시간 배분
    section_times: list[float] = []
    for _, _, weighted in section_syllables:
        ratio = weighted / max(total_weighted_syllables, 1.0)
        section_times.append(total_lyric_time * ratio)

    # SRT 생성
    srt_entries: list[str] = []
    idx = 1
    current_time = INTRO_PAD

    for (sec_name, lines, _), sec_duration in zip(section_syllables, section_times):
        if not lines:
            current_time += sec_duration + SECTION_GAP
            continue

        sec_syllables = sum(_estimate_syllables(l) for l in lines)
        sec_end = current_time + sec_duration

        for line in lines:
            if current_time >= audio_duration - OUTRO_PAD:
                break

            line_syllables = _estimate_syllables(line)
            ratio = line_syllables / max(sec_syllables, 1.0)
            # BPM 기반 타이밍 (음절 × 박자 × 여유 계수)
            bpm_duration = line_syllables * beat * 1.3
            proportion_duration = sec_duration * ratio
            line_dur = max(min_line_dur, min(MAX_LINE_DURATION, bpm_duration, proportion_duration * 1.1))

            end_time = min(current_time + line_dur, sec_end, audio_duration - OUTRO_PAD)
            if end_time <= current_time + 0.1:
                end_time = current_time + min_line_dur

            srt_entries.append(
                f"{idx}\n"
                f"{_seconds_to_srt_time(current_time)} --> {_seconds_to_srt_time(end_time)}\n"
                f"{line}\n"
            )
            idx += 1
            current_time = end_time + LINE_GAP

        # 섹션 전환 공백
        current_time = max(current_time, sec_end) + SECTION_GAP

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(srt_entries), encoding="utf-8")
    logger.info(
        "SRT v2 자막 생성 완료",
        lines=len(srt_entries),
        sections=len(lyric_sections),
        bpm=bpm,
        path=output_path.name,
    )
    return output_path


def generate_srt_for_playlist(
    playlist_id: str,
    track_data: list[dict],  # [{order, duration_seconds, bpm?}]
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
            logger.warning("가사 파일 없음 — 자막 건너뜀", order=order)
            continue

        lyrics = lyrics_path.read_text(encoding="utf-8")
        srt_path = srt_dir / f"track_{order:02d}.srt"
        bpm = float(item.get("bpm", 90))
        generate_srt(
            lyrics_content=lyrics,
            audio_duration=item.get("duration_seconds", 180.0),
            output_path=srt_path,
            bpm=bpm,
        )
        result[order] = srt_path

    return result
