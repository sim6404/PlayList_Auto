"""
Velvet Radio — Phase 3: 썸네일 + 배경 이미지 생성
Flux API (공식 REST) / Apiframe (Midjourney 프록시) 지원
"""
from __future__ import annotations

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
    "Cinematic thumbnail for a YouTube music playlist called '{theme}'. "
    "{concept_visual}. "
    "Warm golden tones, soft bokeh, intimate atmosphere, premium aesthetic. "
    "Text overlay space at bottom. "
    "Style: professional music channel art, editorial photography, "
    "soft light, analog film grain, 16:9 aspect ratio. "
    "No text, no people, no logos. High quality, 4K."
)

BACKGROUND_PROMPT_TEMPLATE = (
    "Seamless looping background for a music video. "
    "{concept_visual}. "
    "Soft, blurred, atmospheric, cinematic. "
    "Velvet-dark edges, warm center glow. "
    "No faces, no text, no sharp objects. "
    "Suitable for overlaying audio spectrum visualization. "
    "1920x1080, moody, elegant, muted color palette."
)

CONCEPT_VISUALS: dict[str, str] = {
    "cozy":            "A warm café corner with steaming coffee and soft lamp glow",
    "nostalgic":       "Vintage record player on a wooden shelf, afternoon light",
    "dreamy":          "Ethereal misty forest path, soft morning light",
    "golden-hour":     "Golden sunset over rooftops, silhouette of city skyline",
    "rainy-day":       "Rain-streaked window with soft city lights blurred outside",
    "sunday-morning":  "Sunlit kitchen with breakfast table, white curtains, flowers",
    "late-night":      "Neon-lit empty street at night, soft rain reflection",
    "breezy":          "Open window with fluttering sheer curtain, ocean in distance",
    "tender":          "Hands holding a warm mug, soft blanket, firelight",
    "hopeful":         "Open road at dawn, horizon glowing with first light",
    "melancholic-soft": "Empty park bench with autumn leaves, golden afternoon",
    "euphoric-gentle": "Cherry blossom petals falling in soft sunlight",
}


class ImageGeneratorClient:
    """Flux / Apiframe 이미지 생성 API 클라이언트"""

    def __init__(self):
        self.api_key = config.image_api_key
        self.api_url = config.image_api_url
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _generate(self, prompt: str, width: int = 1920, height: int = 1080) -> bytes:
        """이미지 생성 → bytes 반환"""
        data = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
        }
        with httpx.Client(timeout=120) as client:
            r = client.post(f"{self.api_url}/imagine", json=data, headers=self.headers)
            r.raise_for_status()
            # Flux는 URL 또는 base64 반환
            resp = r.json()
            if "url" in resp:
                img_r = client.get(resp["url"])
                img_r.raise_for_status()
                return img_r.content
            elif "b64_json" in resp:
                import base64
                return base64.b64decode(resp["b64_json"])
            else:
                raise ValueError(f"예상치 못한 이미지 API 응답: {list(resp.keys())}")

    def generate_thumbnail(
        self,
        playlist: Playlist,
        output_path: Path,
    ) -> Path:
        """플레이리스트 썸네일 생성"""
        dominant_mood = playlist.tracks[0].mood.value if playlist.tracks else "cozy"
        concept_visual = CONCEPT_VISUALS.get(dominant_mood, "A serene, atmospheric scene")
        prompt = THUMBNAIL_PROMPT_TEMPLATE.format(
            theme=playlist.theme,
            concept_visual=concept_visual,
        )
        logger.info("썸네일 생성", playlist_id=playlist.id, mood=dominant_mood)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image_bytes = self._generate(prompt, width=1920, height=1080)
        output_path.write_bytes(image_bytes)
        logger.info("썸네일 저장", path=str(output_path), size_kb=len(image_bytes)//1024)
        return output_path

    def generate_background(
        self,
        playlist: Playlist,
        output_path: Path,
    ) -> Path:
        """영상 배경 이미지 생성"""
        dominant_mood = playlist.tracks[0].mood.value if playlist.tracks else "cozy"
        concept_visual = CONCEPT_VISUALS.get(dominant_mood, "A serene atmospheric scene")
        prompt = BACKGROUND_PROMPT_TEMPLATE.format(concept_visual=concept_visual)
        logger.info("배경 이미지 생성", playlist_id=playlist.id)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image_bytes = self._generate(prompt, width=1920, height=1080)
        output_path.write_bytes(image_bytes)
        logger.info("배경 저장", path=str(output_path))
        return output_path

    def generate_for_playlist(self, playlist: Playlist) -> dict[str, Path]:
        """썸네일 + 배경 이미지 한번에 생성"""
        img_dir = IMAGES_DIR / playlist.id
        img_dir.mkdir(parents=True, exist_ok=True)

        thumbnail_path = img_dir / "thumbnail.jpg"
        background_path = img_dir / "background.jpg"

        try:
            self.generate_thumbnail(playlist, thumbnail_path)
        except Exception as e:
            logger.error("썸네일 생성 실패, 기본 이미지 사용", error=str(e))
            _create_fallback_image(thumbnail_path, playlist.theme)

        try:
            self.generate_background(playlist, background_path)
        except Exception as e:
            logger.error("배경 생성 실패, 기본 이미지 사용", error=str(e))
            _create_fallback_image(background_path, playlist.theme)

        return {"thumbnail": thumbnail_path, "background": background_path}


def _create_fallback_image(output_path: Path, text: str) -> None:
    """Pillow로 기본 그라디언트 이미지 생성 (API 실패 시)"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1920, 1080), color=(30, 25, 40))
        draw = ImageDraw.Draw(img)
        # 간단한 그라디언트 효과
        for i in range(1080):
            alpha = int(255 * (1 - i / 1080) * 0.3)
            draw.line([(0, i), (1920, i)], fill=(80, 60, 100, alpha))
        # 텍스트
        draw.text((960, 540), text, fill=(200, 180, 220), anchor="mm")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "JPEG", quality=95)
    except ImportError:
        # Pillow도 없으면 빈 파일 생성
        output_path.write_bytes(b"")
