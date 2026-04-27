"""
Velvet Radio — Phase 3: 배경 이미지 생성
나노바나나2 API + YouTube 벤치마크 기반 5종 샘플 생성
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import Playlist

logger = get_logger(__name__)

IMAGES_DIR = config.data_dir / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# ── 이미지 프롬프트 템플릿 ──────────────────────────────────────────

THUMBNAIL_PROMPT_TEMPLATE = (
    "Cinematic YouTube music playlist thumbnail for '{theme}'. "
    "{concept_visual}. "
    "Warm golden tones, soft bokeh, intimate atmosphere, premium aesthetic. "
    "Professional music channel art, editorial photography, soft light, analog film grain. "
    "16:9 aspect ratio. No text, no people, no logos. High quality, 4K."
)

# 5가지 배경 스타일 변형 (무드 기반 + 스타일 변주)
BACKGROUND_STYLE_VARIANTS = [
    # Variant 1: Primary / Cinematic
    (
        "Cinematic atmospheric music video background, {concept_visual}. "
        "Soft blurred bokeh, moody warm tones, velvety dark edges with glowing center. "
        "No faces, no text. 1920x1080, elegant, muted color palette. "
        "{benchmark_style}"
    ),
    # Variant 2: Abstract / Painterly
    (
        "Abstract painterly music video background, {concept_visual}. "
        "Impressionistic brush strokes, ethereal light leaks, dream-like atmosphere. "
        "Soft watercolor tones, gentle color gradients. No faces, no text. 1920x1080. "
        "{benchmark_style}"
    ),
    # Variant 3: Nature / Landscape
    (
        "Serene nature landscape music video background, {concept_visual}. "
        "Rolling hills, misty morning, golden hour light through trees. "
        "Peaceful, timeless, cinematic depth of field. No faces, no text. 1920x1080. "
        "{benchmark_style}"
    ),
    # Variant 4: Urban / Architectural
    (
        "Stylized urban architecture music video background, {concept_visual}. "
        "Soft-focus city lights, window reflections, geometric patterns with warm glow. "
        "Contemporary, sophisticated, ambient mood. No faces, no text. 1920x1080. "
        "{benchmark_style}"
    ),
    # Variant 5: Minimalist / Texture
    (
        "Minimalist textured music video background, {concept_visual}. "
        "Fine grain film texture, subtle color wash, pure atmospheric mood. "
        "Elegant simplicity, monochromatic with warm accent. No faces, no text. 1920x1080. "
        "{benchmark_style}"
    ),
]

CONCEPT_VISUALS: dict[str, str] = {
    "cozy":            "warm café corner with steaming coffee and soft lamp glow",
    "nostalgic":       "vintage record player on wooden shelf in afternoon light",
    "dreamy":          "ethereal misty forest path with soft morning light",
    "golden-hour":     "golden sunset over rooftops, silhouette of city skyline",
    "rainy-day":       "rain-streaked window with soft city lights blurred outside",
    "sunday-morning":  "sunlit kitchen with breakfast table, white curtains, flowers",
    "late-night":      "neon-lit empty street at night, soft rain reflections",
    "breezy":          "open window with fluttering sheer curtain, ocean in distance",
    "tender":          "hands holding warm mug, soft blanket, gentle firelight",
    "hopeful":         "open road at dawn, horizon glowing with first light",
    "melancholic-soft": "empty park bench with autumn leaves, golden afternoon",
    "euphoric-gentle": "cherry blossom petals falling in soft sunlight",
}


# ── Google AI Studio 이미지 생성 클라이언트 ──────────────────────────────

class NanoBanana2Client:
    """
    Google AI Studio (Gemini Image) 이미지 생성 클라이언트
    환경변수:
        NANOBANANA2_API_KEY  = Google AI Studio API 키
        NANOBANANA2_API_URL  = https://generativelanguage.googleapis.com/v1beta
        GEMINI_IMAGE_MODEL   = gemini-2.5-flash-image (기본값)
    """

    def __init__(self):
        import os
        self.api_key = os.environ.get("NANOBANANA2_API_KEY") or os.environ.get("IMAGE_API_KEY", "")
        self.api_url = os.environ.get(
            "NANOBANANA2_API_URL",
            os.environ.get("IMAGE_API_URL", "https://generativelanguage.googleapis.com/v1beta"),
        ).rstrip("/")
        self.model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

    def generate(
        self,
        prompt: str,
        width: int = 1920,
        height: int = 1080,
        seed: Optional[int] = None,
        steps: int = 28,
    ) -> bytes:
        """
        Google AI Studio Gemini Image API로 이미지 생성 → JPEG bytes 반환

        요청 형식:
          POST /v1beta/models/{model}:generateContent?key={API_KEY}
          {"contents":[{"parts":[{"text":"..."}]}],
           "generationConfig":{"responseModalities":["IMAGE","TEXT"]}}

        응답에서 inlineData.data (base64) 추출 → PNG bytes → JPEG 변환
        """
        import base64

        # 1920×1080 비율 힌트를 프롬프트에 포함
        full_prompt = f"{prompt} Wide cinematic 16:9 aspect ratio, 1920x1080 resolution."

        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
        }

        endpoint = f"{self.api_url}/models/{self.model}:generateContent?key={self.api_key}"

        with httpx.Client(timeout=120) as client:
            r = client.post(endpoint, json=payload)
            r.raise_for_status()
            resp = r.json()

        # 응답에서 이미지 파트 추출
        parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData")
            if inline:
                img_bytes = base64.b64decode(inline["data"])
                mime = inline.get("mimeType", "image/png")
                # PNG → JPEG 변환 (파일 크기 최적화)
                return _convert_to_jpeg(img_bytes)

        raise ValueError(f"Gemini 응답에 이미지 파트 없음: {list(resp.keys())}")


def _convert_to_jpeg(img_bytes: bytes, quality: int = 92) -> bytes:
    """이미지 bytes → JPEG bytes 변환 (Pillow 사용, 없으면 원본 반환)"""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return img_bytes  # Pillow 없거나 실패 시 원본 반환


# ── YouTube 벤치마크 분석기 ────────────────────────────────────────────

class YouTubeBenchmarker:
    """
    동일 장르 YouTube 상위 1~5위 영상 배경 스타일 분석
    YouTube Data API v3 사용
    """

    def __init__(self):
        import os
        self.api_key = os.environ.get("YOUTUBE_DATA_API_KEY", "")
        self.enabled = bool(self.api_key)

    def find_top_videos(self, genre: str, mood: str, count: int = 5) -> list[dict]:
        """동일 장르 상위 영상 검색"""
        if not self.enabled:
            logger.warning("YouTube Data API 키 없음 — 벤치마크 건너뜀")
            return []

        query = f"{genre} {mood} music playlist lofi relaxing"
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": count,
            "order": "viewCount",
            "videoCategoryId": "10",  # Music
            "key": self.api_key,
        }
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()

            videos = []
            for item in data.get("items", []):
                snip = item.get("snippet", {})
                thumbnails = snip.get("thumbnails", {})
                thumb_url = (
                    thumbnails.get("maxres", {}).get("url")
                    or thumbnails.get("high", {}).get("url")
                    or thumbnails.get("medium", {}).get("url", "")
                )
                videos.append({
                    "video_id": item.get("id", {}).get("videoId", ""),
                    "title": snip.get("title", ""),
                    "thumbnail_url": thumb_url,
                })
            logger.info("YouTube 벤치마크 검색 완료", query=query, count=len(videos))
            return videos[:count]

        except Exception as e:
            logger.warning("YouTube 벤치마크 검색 실패", error=str(e))
            return []

    def analyze_visual_style(self, videos: list[dict]) -> str:
        """
        상위 영상 썸네일 분석 → 스타일 키워드 추출
        Claude Vision API 사용
        """
        if not videos:
            return ""

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=config.anthropic_api_key)

            # 썸네일 URL 목록 메시지 구성 (텍스트 설명으로 분석)
            titles = [v["title"] for v in videos if v.get("title")]
            title_text = "\n".join(f"- {t}" for t in titles)

            prompt = (
                f"다음은 YouTube에서 조회수 상위권인 이지리스닝/로파이 뮤직 플레이리스트 영상 제목들입니다:\n"
                f"{title_text}\n\n"
                f"이 영상들의 배경 이미지에서 공통적으로 나타나는 시각적 스타일 요소를 "
                f"영어로 5~10개 키워드로 추출해 주세요. "
                f"예: 'warm bokeh, vintage film grain, golden hour, soft focus, cozy interior'"
            )

            resp = client.messages.create(
                model=config.claude_model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            style_text = resp.content[0].text.strip()
            logger.info("YouTube 스타일 분석 완료", style_preview=style_text[:80])
            return style_text

        except Exception as e:
            logger.warning("스타일 분석 실패", error=str(e))
            return ""


# ── 메인 이미지 생성 클라이언트 ──────────────────────────────────────────

class ImageGeneratorClient:
    """나노바나나2 API + YouTube 벤치마크 기반 배경 이미지 5종 생성"""

    def __init__(self):
        self.nb2 = NanoBanana2Client()
        self.benchmarker = YouTubeBenchmarker()

    def _get_benchmark_style(self, playlist: Playlist) -> str:
        """YouTube 상위 영상 분석으로 스타일 힌트 추출"""
        dominant_mood = playlist.tracks[0].mood.value if playlist.tracks else "cozy"
        dominant_genre = playlist.tracks[0].sub_genre if playlist.tracks else "easy listening"
        videos = self.benchmarker.find_top_videos(dominant_genre, dominant_mood)
        if not videos:
            return ""
        style = self.benchmarker.analyze_visual_style(videos)
        return f"Inspired by trending styles: {style}" if style else ""

    def generate_background_samples(
        self,
        playlist: Playlist,
        count: int = 5,
    ) -> list[Path]:
        """
        나노바나나2 API로 배경 이미지 5종 샘플 생성

        Returns:
            [background_sample_1.jpg, ..., background_sample_5.jpg]
        """
        img_dir = IMAGES_DIR / playlist.id
        img_dir.mkdir(parents=True, exist_ok=True)

        dominant_mood = playlist.tracks[0].mood.value if playlist.tracks else "cozy"
        concept_visual = CONCEPT_VISUALS.get(dominant_mood, "a serene atmospheric scene")

        # YouTube 벤치마크 스타일 (실패해도 빈 문자열로 계속)
        try:
            benchmark_style = self._get_benchmark_style(playlist)
        except Exception:
            benchmark_style = ""

        samples: list[Path] = []
        variants_to_use = BACKGROUND_STYLE_VARIANTS[:count]

        for i, template in enumerate(variants_to_use, start=1):
            out_path = img_dir / f"background_sample_{i}.jpg"

            # 이미 생성된 샘플이 있으면 재사용
            if out_path.exists() and out_path.stat().st_size > 10_000:
                logger.info("기존 샘플 재사용", sample=i, path=out_path.name)
                samples.append(out_path)
                continue

            prompt = template.format(
                concept_visual=concept_visual,
                benchmark_style=benchmark_style,
            )

            try:
                logger.info("배경 샘플 생성 중", variant=i, mood=dominant_mood)
                img_bytes = self.nb2.generate(
                    prompt=prompt,
                    width=1920,
                    height=1080,
                    seed=i * 1000 + hash(playlist.id) % 1000,
                )
                out_path.write_bytes(img_bytes)
                logger.info("배경 샘플 저장", variant=i, size_kb=len(img_bytes) // 1024)
            except Exception as e:
                logger.error("나노바나나2 샘플 생성 실패", variant=i, error=str(e))
                _create_fallback_image(out_path, playlist.theme, variant=i)

            samples.append(out_path)
            # API 레이트 리밋 방지
            if i < count:
                time.sleep(1.0)

        return samples

    def generate_thumbnail(self, playlist: Playlist, output_path: Path) -> Path:
        """플레이리스트 썸네일 생성 (나노바나나2 API)"""
        dominant_mood = playlist.tracks[0].mood.value if playlist.tracks else "cozy"
        concept_visual = CONCEPT_VISUALS.get(dominant_mood, "a serene atmospheric scene")
        prompt = THUMBNAIL_PROMPT_TEMPLATE.format(
            theme=playlist.theme,
            concept_visual=concept_visual,
        )
        logger.info("썸네일 생성 중", playlist_id=playlist.id)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            img_bytes = self.nb2.generate(prompt=prompt, width=1920, height=1080)
            output_path.write_bytes(img_bytes)
            logger.info("썸네일 저장", path=output_path.name, size_kb=len(img_bytes) // 1024)
        except Exception as e:
            logger.error("썸네일 생성 실패 — 폴백", error=str(e))
            _create_fallback_image(output_path, playlist.theme, variant=0)

        return output_path

    def generate_for_playlist(self, playlist: Playlist) -> dict[str, Path]:
        """
        썸네일 + 배경 5종 샘플 생성

        Returns:
            {
                "thumbnail": Path,
                "background": Path,           # 기본 선택 (sample_1)
                "background_samples": [Path]  # 5종 전체
            }
        """
        img_dir = IMAGES_DIR / playlist.id
        img_dir.mkdir(parents=True, exist_ok=True)

        thumbnail_path = img_dir / "thumbnail.jpg"
        self.generate_thumbnail(playlist, thumbnail_path)

        samples = self.generate_background_samples(playlist, count=5)
        background_path = samples[0] if samples else img_dir / "background_sample_1.jpg"

        return {
            "thumbnail": thumbnail_path,
            "background": background_path,
            "background_samples": samples,
        }


# ── 폴백 이미지 ───────────────────────────────────────────────────────

_VARIANT_COLORS = [
    (30, 20, 40),   # 딥 퍼플 (기본)
    (20, 30, 40),   # 딥 블루 (추상)
    (20, 35, 25),   # 포레스트 그린 (자연)
    (35, 25, 20),   # 워밍 브라운 (도시)
    (15, 15, 20),   # 미드나잇 (미니멀)
]


def _create_fallback_image(output_path: Path, text: str, variant: int = 0) -> None:
    """Pillow로 기본 그라디언트 이미지 생성 (API 실패 시 폴백)"""
    try:
        from PIL import Image, ImageDraw
        base_color = _VARIANT_COLORS[variant % len(_VARIANT_COLORS)]
        img = Image.new("RGB", (1920, 1080), color=base_color)
        draw = ImageDraw.Draw(img)
        # 간단한 그라디언트 효과
        for i in range(1080):
            t = i / 1080
            r = int(base_color[0] + 40 * (1 - t))
            g = int(base_color[1] + 30 * (1 - t))
            b = int(base_color[2] + 50 * (1 - t))
            draw.line([(0, i), (1920, i)], fill=(min(255, r), min(255, g), min(255, b)))
        # 텍스트 (테마명)
        draw.text((960, 520), text, fill=(200, 180, 220), anchor="mm")
        draw.text((960, 560), f"Sample {variant}", fill=(150, 130, 170), anchor="mm")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "JPEG", quality=92)
    except ImportError:
        output_path.write_bytes(b"")
