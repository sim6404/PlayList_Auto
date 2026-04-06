"""
Velvet Radio — Phase 1: Suno 스타일 프롬프트 조립
규칙 기반 (API 호출 없음) — Track + channel_dna → StylePrompt
"""
from __future__ import annotations

from ..common.config_loader import dna
from ..common.logger import get_logger
from ..common.models import Playlist, StylePrompt, SunoPayload, Track

logger = get_logger(__name__)

# 서브장르별 악기 매핑
INSTRUMENT_MAP: dict[str, list[str]] = {
    "acoustic bossa nova pop":      ["nylon guitar", "soft percussion", "light piano"],
    "soft jazz pop":                ["jazz piano", "upright bass", "brushed drums"],
    "lo-fi dream pop":              ["lo-fi keys", "ambient pads", "tape-saturated drums"],
    "warm retro city pop":          ["electric piano", "funk guitar", "warm synths"],
    "mellow folk pop":              ["acoustic guitar", "gentle strings", "soft harmonica"],
    "ambient indie pop":            ["ambient guitar", "synthesizer pads", "minimal percussion"],
    "neo-soul light":               ["rhodes piano", "light funk guitar", "soft drums"],
    "smooth adult contemporary":    ["piano", "strings", "soft guitar"],
}

# 무드별 감성 디스크립터 (V5 최적화)
MOOD_DESCRIPTORS: dict[str, str] = {
    "cozy":             "warm, intimate, fireplace glow",
    "nostalgic":        "wistful, cinematic, hazy memory",
    "dreamy":           "ethereal, floating, soft-focus",
    "golden-hour":      "luminous, expansive, golden warmth",
    "rainy-day":        "melancholic, tender, window-pane reverie",
    "sunday-morning":   "languid, bright, unhurried",
    "late-night":       "velvet darkness, introspective, yearning",
    "breezy":           "light, carefree, open-air",
    "tender":           "vulnerable, close, heartfelt",
    "hopeful":          "uplifting, soft sunrise, forward-gazing",
    "melancholic-soft": "bittersweet, raw, quietly aching",
    "euphoric-gentle":  "blissful, expansive, glowing joy",
}

# 생성 품질 강화 태그 (채널 표준)
PRODUCTION_TAGS = [
    "radio-ready mix",
    "wide stereo field",
    "analog warmth",
    "warm reverb",
    "soft saturation",
]

NEGATIVE_TAGS = [
    "no EDM drops",
    "no heavy metal",
    "no aggressive beats",
    "no autotune excess",
    "no trap hi-hats",
    "no distortion",
    "no dubstep",
    "no screaming",
]


def assemble_style_prompt(track: Track) -> StylePrompt:
    """
    Top-Loaded Palette 공식:
    [Mood Descriptor] + [Genre] + [2-3 Instruments] + [Vocal Identity] + [BPM/Key] + [Production]

    Suno V5 권장: 감정 디스크립터를 앞에, 네거티브를 끝에
    """
    parts: list[str] = []

    # 1. Mood descriptor (최우선)
    mood_key = track.mood.value
    descriptor = MOOD_DESCRIPTORS.get(mood_key, mood_key)
    parts.append(descriptor)

    # 2. Sub-genre
    parts.append(track.sub_genre)

    # 3. Instruments (최대 3개)
    instruments = INSTRUMENT_MAP.get(track.sub_genre, ["piano", "acoustic guitar", "soft percussion"])
    parts.extend(instruments[:3])

    # 4. Vocal identity
    parts.append(track.vocal.value)

    # 5. BPM + Key
    parts.append(f"{track.bpm} BPM")
    parts.append(track.key)

    # 6. Production tags
    parts.extend(PRODUCTION_TAGS)

    # 7. Hook 강화 (초반 3곡)
    if track.hook_priority:
        parts.append("memorable hook, instant appeal, strong opening")

    prompt = ", ".join(parts)
    negative = ", ".join(NEGATIVE_TAGS)

    # 1,000자 제한 강제
    if len(prompt) > 950:
        prompt = prompt[:950].rsplit(",", 1)[0]
        logger.warning("스타일 프롬프트 950자 초과로 잘림", track=track.order, length=len(prompt))

    return StylePrompt(
        track_order=track.order,
        prompt=prompt,
        negative=negative,
    )


def build_suno_payload(track: Track, lyrics_content: str) -> SunoPayload:
    """StylePrompt + Lyrics → SunoPayload 조립"""
    style = assemble_style_prompt(track)

    # 네거티브 프롬프트를 스타일 끝에 붙이기 (Suno V5 권장)
    full_prompt = f"{style.prompt}\n\nNegative: {style.negative}"

    return SunoPayload(
        track_order=track.order,
        lyrics=lyrics_content,
        style_prompt=full_prompt[:1000],  # 최종 1,000자 제한
        model="v5",
        instrumental=False,
        title=track.title,
    )


def build_all_payloads(
    playlist: Playlist,
    lyrics_map: dict[int, str],  # {track_order: lyrics_content}
) -> list[SunoPayload]:
    """플레이리스트 전체 SunoPayload 목록 생성"""
    payloads: list[SunoPayload] = []
    for track in playlist.tracks:
        lyrics = lyrics_map.get(track.order, "")
        if not lyrics:
            logger.warning("가사 없음, 인스트루멘탈로 대체", track=track.order)
            payload = SunoPayload(
                track_order=track.order,
                lyrics="[Instrumental]",
                style_prompt=assemble_style_prompt(track).prompt,
                model="v5",
                instrumental=True,
                title=track.title,
            )
        else:
            payload = build_suno_payload(track, lyrics)
        payloads.append(payload)

    logger.info("SunoPayload 생성 완료", count=len(payloads))
    return payloads
