"""
Velvet Radio — Phase 5: DistroKid 업로드 패키지 준비
선별된 트랙을 DistroKid 업로드 규격에 맞게 패키징
(DistroKid는 공식 API 없으므로 파일 패키지 준비만 자동화)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import QualityReport

logger = get_logger(__name__)

EXPORTS_DIR = config.data_dir / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def prepare_distrokid_package(
    playlist_id: str,
    quality_report: QualityReport,
    playlist_theme: str,
    thumbnail_path: Path,
) -> Path:
    """
    DistroKid 업로드용 패키지 생성

    패키지 구조:
    exports/{playlist_id}/
    ├── tracks/
    │   ├── 01_track_title.mp3
    │   └── ...
    ├── artwork/
    │   └── cover.jpg (3000×3000 최소)
    ├── metadata.json
    └── README.txt

    Returns:
        패키지 디렉토리 경로
    """
    out_dir = EXPORTS_DIR / playlist_id
    tracks_dir = out_dir / "tracks"
    artwork_dir = out_dir / "artwork"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    artwork_dir.mkdir(parents=True, exist_ok=True)

    # 1. 음원 복사 (선별된 트랙만)
    copied = 0
    for asset in sorted(quality_report.assets, key=lambda x: x.track_order):
        if not asset.selected:
            continue

        src = Path(asset.file_path)
        if not src.exists():
            continue

        # 파일명 정규화 (DistroKid 권장: 숫자_제목.mp3)
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in asset.title)
        dst_name = f"{asset.track_order:02d}_{safe_title[:50]}.mp3"
        shutil.copy2(src, tracks_dir / dst_name)
        copied += 1

    # 2. 커버 아트 복사 (3000×3000 권장)
    if thumbnail_path.exists():
        shutil.copy2(thumbnail_path, artwork_dir / "cover.jpg")

    # 3. 메타데이터 JSON 생성
    metadata = {
        "release_title": playlist_theme,
        "artist_name": "Velvet Radio",
        "label": "Velvet Radio Records",
        "release_date": "",  # 마스터가 수동 입력
        "genre": "Easy Listening",
        "sub_genre": "Pop",
        "language": "English",
        "explicit_content": False,
        "ai_generated": True,
        "ai_tool": "Suno AI v5",
        "copyright_holder": "Velvet Radio",
        "upc": "",  # DistroKid 자동 생성
        "tracks": [
            {
                "track_number": a.track_order,
                "title": a.title,
                "isrc": "",  # DistroKid 자동 생성
                "duration_seconds": int(a.duration_seconds),
            }
            for a in sorted(quality_report.assets, key=lambda x: x.track_order)
            if a.selected
        ],
    }

    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. 안내 파일
    readme = (
        "# Velvet Radio — DistroKid 업로드 가이드\n\n"
        "1. DistroKid (distrokid.com) 로그인\n"
        "2. 'Upload Music' 선택\n"
        "3. metadata.json 참조하여 정보 입력\n"
        "4. tracks/ 폴더의 mp3 파일 순서대로 업로드\n"
        "5. artwork/cover.jpg 를 커버 이미지로 업로드\n"
        "6. AI 생성 음악 체크박스 반드시 선택\n\n"
        "⚠️ 중요: 'Contains AI-generated content' 반드시 체크!\n"
        "⚠️ 업로드 빈도: 주 1~2회 이하 유지 (스팸 플래그 방지)\n"
    )
    (out_dir / "README.txt").write_text(readme, encoding="utf-8")

    logger.info(
        "DistroKid 패키지 준비 완료",
        playlist_id=playlist_id,
        tracks=copied,
        path=str(out_dir),
    )
    return out_dir
