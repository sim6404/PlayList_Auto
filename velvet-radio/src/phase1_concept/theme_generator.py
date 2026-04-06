"""
Velvet Radio — Phase 1: 플레이리스트 테마 + 트랙리스트 생성
Claude API 호출 → Playlist 모델 생성 → data/playlists/ 저장
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..common.claude_client import get_claude_client
from ..common.config_loader import config, dna, load_prompt
from ..common.logger import get_logger
from ..common.models import Mood, Playlist, Track, VocalPersona

logger = get_logger(__name__)

PLAYLISTS_DIR = config.data_dir / "playlists"
PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_theme_history() -> list[str]:
    """이전 플레이리스트 테마 목록 (중복 방지용)"""
    themes = []
    for f in sorted(PLAYLISTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            themes.append(data.get("theme", ""))
        except Exception:
            continue
    return themes


def _parse_track(raw: dict, order: int) -> Track:
    """원시 dict → Track 모델 변환 (관대한 파싱)"""
    # VocalPersona 매핑 (부분 일치 허용)
    vocal_raw = raw.get("vocal", "VR_F1")
    vocal = VocalPersona.VR_F1  # 기본값
    for persona in VocalPersona:
        if persona.value in vocal_raw or vocal_raw in persona.name:
            vocal = persona
            break

    # Mood 매핑
    mood_raw = raw.get("mood", "cozy")
    try:
        mood = Mood(mood_raw)
    except ValueError:
        mood = Mood.COZY
        logger.warning("알 수 없는 mood, 기본값 사용", raw=mood_raw)

    return Track(
        order=order,
        title=raw.get("title", f"Track {order}"),
        mood=mood,
        sub_genre=raw.get("sub_genre", "soft jazz pop"),
        bpm=max(72, min(108, int(raw.get("bpm", 85)))),
        key=raw.get("key", "G major"),
        vocal=vocal,
        hook_priority=order <= 3,
        concept_note=raw.get("concept_note"),
    )


def generate_playlist() -> Playlist:
    """
    플레이리스트 테마 + 20곡 트랙리스트 생성

    Returns:
        Playlist: 생성된 플레이리스트 (data/playlists/ 에 저장됨)
    """
    client = get_claude_client()
    system_prompt = load_prompt("system_theme")
    channel_data = {
        "music_identity": dna.music_identity,
        "vocal_personas": {k: v["description"] for k, v in dna.vocal_personas.items()},
        "playlist_structure": dna.playlist_structure,
        "previous_themes": _load_theme_history()[-10:],  # 최근 10개만
        "today": datetime.now().strftime("%Y-%m-%d"),
    }

    logger.info("테마 생성 시작", history_count=len(channel_data["previous_themes"]))

    raw = client.generate_theme(system_prompt, channel_data)

    # playlist_id 자동 생성 (없는 경우)
    if "playlist_id" not in raw or not raw["playlist_id"]:
        slug = re.sub(r"[^a-z0-9]+", "-", raw.get("theme", "untitled").lower()).strip("-")
        raw["playlist_id"] = f"{datetime.now().strftime('%Y%m%d')}_{slug}"

    # 트랙 파싱
    raw_tracks = raw.get("tracks", [])
    tracks = [_parse_track(t, i + 1) for i, t in enumerate(raw_tracks[:20])]

    # 최소 15곡 확인
    if len(tracks) < 15:
        raise ValueError(f"생성된 트랙 수 부족: {len(tracks)}개 (최소 15개 필요)")

    playlist = Playlist(
        id=raw["playlist_id"],
        theme=raw.get("theme", "Untitled Playlist"),
        concept=raw.get("concept", ""),
        tracks=tracks,
    )

    # 저장
    out_path = PLAYLISTS_DIR / f"{playlist.id}.json"
    out_path.write_text(
        playlist.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    logger.info(
        "플레이리스트 생성 완료",
        playlist_id=playlist.id,
        theme=playlist.theme,
        track_count=len(playlist.tracks),
        path=str(out_path),
    )
    return playlist


def load_playlist(playlist_id: str) -> Playlist:
    """저장된 플레이리스트 로드"""
    path = PLAYLISTS_DIR / f"{playlist_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"플레이리스트를 찾을 수 없습니다: {playlist_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Playlist(**data)
