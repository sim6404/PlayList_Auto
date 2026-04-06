"""
Velvet Radio — Phase 4: 다국어 SEO 메타데이터 생성
Claude API → YouTube 최적화 제목/설명/태그/해시태그 생성
"""
from __future__ import annotations

import json
from pathlib import Path

from ..common.claude_client import get_claude_client
from ..common.config_loader import config, dna
from ..common.logger import get_logger
from ..common.models import Playlist, QualityReport, SEOMetadata, VideoAsset

logger = get_logger(__name__)

SEO_SYSTEM_PROMPT = """
You are an expert YouTube SEO strategist and copywriter for "Velvet Radio," a premium easy-listening pop music channel.

Your goal: generate metadata that maximizes YouTube algorithm discovery AND human click-through rate.

## YOUTUBE SEO RULES
1. **Title (EN)**: 60–80 chars. Front-load primary keyword. Include emotional hook. Format options:
   - "[Mood] [Genre] Playlist | [Emotional Descriptor] — Velvet Radio"
   - "[Hours] of [Genre] | [Theme] | Velvet Radio"
2. **Description (EN)**:
   - First 150 chars: primary keyword + value proposition (shown in search results)
   - Lines 151–500: track list with chapter timestamps
   - Lines 501–800: channel description + secondary keywords
   - Lines 801+: hashtags (3–5 relevant ones)
3. **Tags**: 15–25 tags. Mix of: exact match, broad match, long-tail, channel brand.
   Never exceed YouTube's 500-char tag limit.
4. **Multilingual**: Also generate titles + first 150 chars of description in Japanese and Brazilian Portuguese (high YouTube RPM markets).

## OUTPUT FORMAT (strict JSON)
```json
{
  "title_en": "Easy Listening Pop Playlist | Sunset Drive Vibes — Velvet Radio",
  "title_ja": "イージーリスニングポップ | サンセットドライブ — Velvet Radio",
  "title_pt": "Playlist Easy Listening | Vibrações ao Pôr do Sol — Velvet Radio",
  "title_ko": "이지리스닝 팝 플레이리스트 | 선셋 드라이브 — Velvet Radio",
  "description_en": "Full 150-char hook here...\n\n📋 TRACKLIST\n00:00 Intro\n00:30 01. Track Title\n...\n\n🎙️ About Velvet Radio...\n\n#VelvetRadio #EasyListening",
  "description_ja": "Japanese description first 150 chars...",
  "description_pt": "Portuguese description first 150 chars...",
  "tags": ["easy listening", "chill music", "study music", ...],
  "hashtags": ["#VelvetRadio", "#EasyListening", "#ChillMusic", "#StudyMusic", "#RelaxingMusic"]
}
```

Return ONLY valid JSON. No markdown. No explanation.
"""


def _build_tracklist_text(tracks: list, chapter_timestamps: list[str]) -> str:
    """트랙 목록 + 타임스탬프 텍스트 생성"""
    if chapter_timestamps:
        return "\n".join(chapter_timestamps)

    lines = []
    for i, t in enumerate(tracks):
        title = t.get("title") if isinstance(t, dict) else t.title
        lines.append(f"{i+1:02d}. {title}")
    return "\n".join(lines)


def generate_seo_metadata(
    playlist: Playlist,
    video_asset: VideoAsset,
    quality_report: QualityReport,
    chapter_timestamps: list[str] | None = None,
) -> SEOMetadata:
    """
    Claude API로 다국어 SEO 메타데이터 생성

    Returns:
        SEOMetadata 객체
    """
    client = get_claude_client()
    channel_seo = dna.seo

    duration_h = video_asset.duration_seconds // 3600
    duration_m = (video_asset.duration_seconds % 3600) // 60
    duration_text = f"{duration_h}h {duration_m}m" if duration_h else f"{duration_m}m"

    tracklist_text = _build_tracklist_text(
        playlist.tracks,
        chapter_timestamps or [],
    )

    playlist_data = {
        "theme": playlist.theme,
        "concept": playlist.concept,
        "duration": duration_text,
        "track_count": quality_report.selected_count,
        "primary_keywords": channel_seo["primary_keywords"],
        "secondary_keywords": channel_seo["secondary_keywords"],
        "channel_name": "Velvet Radio",
        "channel_url": "https://www.youtube.com/@VelvetRadio",
        "tracklist": tracklist_text,
        "target_languages": channel_seo["target_languages"],
        "hashtag_groups": channel_seo["hashtag_groups"],
        "dominant_mood": playlist.tracks[0].mood.value if playlist.tracks else "cozy",
        "sub_genres": list({t.sub_genre for t in playlist.tracks}),
    }

    logger.info("SEO 메타데이터 생성 시작", playlist_id=playlist.id)

    raw = client.generate_seo(SEO_SYSTEM_PROMPT, playlist_data, [])

    # 필수 필드 폴백
    if "title_en" not in raw:
        raw["title_en"] = (
            f"Easy Listening Pop Playlist | {playlist.theme} — Velvet Radio"
        )[:100]

    if "description_en" not in raw:
        raw["description_en"] = (
            f"♫ {playlist.theme} — {duration_text} of pure easy-listening bliss. "
            f"Study, relax, and unwind with Velvet Radio.\n\n"
            f"📋 TRACKLIST\n{tracklist_text}\n\n"
            f"🎙️ Velvet Radio — Your daily sanctuary of easy-listening pop.\n"
            f"Subscribe for new playlists every Tuesday & Friday.\n\n"
            f"{'  '.join(channel_seo['hashtag_groups']['core'])}"
        )

    # 태그 기본값
    if "tags" not in raw or not raw["tags"]:
        raw["tags"] = (
            channel_seo["primary_keywords"]
            + channel_seo["secondary_keywords"]
            + [playlist.theme.lower(), "velvet radio playlist"]
        )

    seo = SEOMetadata(
        playlist_id=playlist.id,
        title_en=raw.get("title_en", "")[:100],
        title_ja=raw.get("title_ja"),
        title_pt=raw.get("title_pt"),
        title_ko=raw.get("title_ko"),
        description_en=raw.get("description_en", ""),
        description_ja=raw.get("description_ja"),
        description_pt=raw.get("description_pt"),
        tags=raw.get("tags", [])[:500],   # YouTube 500자 제한
        hashtags=raw.get("hashtags", channel_seo["hashtag_groups"]["core"]),
        chapter_timestamps=chapter_timestamps or [],
    )

    # 저장
    out_path = config.data_dir / "playlists" / f"{playlist.id}_seo.json"
    out_path.write_text(seo.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    logger.info("SEO 메타데이터 저장", path=str(out_path), title=seo.title_en)
    return seo
