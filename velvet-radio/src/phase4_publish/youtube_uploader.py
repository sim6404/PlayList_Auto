"""
Velvet Radio — Phase 4: YouTube Data API v3 업로더
OAuth2 인증 + Resumable Upload + 플레이리스트 관리 + 썸네일 설정
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import SEOMetadata, UploadPrivacy, VideoAsset, YouTubeUpload

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# YouTube API 쿼터 제한 대응 (1,600 유닛/업로드)
UPLOAD_RETRY_MAX = 3
UPLOAD_RETRY_DELAY = 5


class YouTubeUploader:
    """YouTube Data API v3 업로더"""

    def __init__(self):
        self.credentials = self._build_credentials()
        self.service = build("youtube", "v3", credentials=self.credentials)
        logger.info("YouTubeUploader 초기화 완료")

    def _build_credentials(self) -> Credentials:
        """OAuth2 자격증명 빌드 (refresh token 기반)"""
        creds = Credentials(
            token=None,
            refresh_token=config.youtube_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.youtube_client_id,
            client_secret=config.youtube_client_secret,
            scopes=SCOPES,
        )
        # 토큰 갱신
        creds.refresh(Request())
        return creds

    def upload_video(
        self,
        upload: YouTubeUpload,
    ) -> str:
        """
        영상 Resumable Upload

        Returns:
            YouTube video_id
        """
        video_path = Path(upload.video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"영상 파일 없음: {upload.video_path}")

        file_size_mb = video_path.stat().st_size / 1_048_576
        logger.info(
            "YouTube 업로드 시작",
            title=upload.title[:50],
            size_mb=f"{file_size_mb:.0f}MB",
        )

        body = {
            "snippet": {
                "title": upload.title,
                "description": upload.description,
                "tags": upload.tags,
                "categoryId": upload.category_id,
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": upload.privacy.value,
                "selfDeclaredMadeForKids": False,
            },
        }

        if upload.scheduled_at:
            body["status"]["publishAt"] = upload.scheduled_at
            body["status"]["privacyStatus"] = "private"  # 예약 시 반드시 private

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10MB 청크
        )

        for attempt in range(1, UPLOAD_RETRY_MAX + 1):
            try:
                request = self.service.videos().insert(
                    part="snippet,status",
                    body=body,
                    media_body=media,
                )

                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        pct = int(status.progress() * 100)
                        logger.info("업로드 진행", progress=f"{pct}%")

                video_id = response["id"]
                logger.info("업로드 완료", video_id=video_id)
                return video_id

            except HttpError as e:
                if e.resp.status in (500, 502, 503, 504) and attempt < UPLOAD_RETRY_MAX:
                    wait = UPLOAD_RETRY_DELAY * attempt
                    logger.warning(f"업로드 실패 {attempt}회, {wait}초 후 재시도", status=e.resp.status)
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError(f"YouTube 업로드 {UPLOAD_RETRY_MAX}회 실패")

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> bool:
        """커스텀 썸네일 설정"""
        if not thumbnail_path.exists():
            logger.warning("썸네일 파일 없음", path=str(thumbnail_path))
            return False
        try:
            self.service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
            ).execute()
            logger.info("썸네일 설정 완료", video_id=video_id)
            return True
        except HttpError as e:
            logger.error("썸네일 설정 실패", error=str(e))
            return False

    def create_or_get_playlist(self, title: str, description: str) -> str:
        """채널 플레이리스트 생성 또는 기존 것 반환"""
        # 기존 플레이리스트 검색
        try:
            result = self.service.playlists().list(
                part="snippet",
                mine=True,
                maxResults=50,
            ).execute()

            for item in result.get("items", []):
                if item["snippet"]["title"] == title:
                    playlist_id = item["id"]
                    logger.info("기존 플레이리스트 사용", playlist_id=playlist_id)
                    return playlist_id
        except HttpError:
            pass

        # 새 플레이리스트 생성
        response = self.service.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "defaultLanguage": "en",
                },
                "status": {"privacyStatus": "public"},
            },
        ).execute()

        playlist_id = response["id"]
        logger.info("플레이리스트 생성", playlist_id=playlist_id, title=title)
        return playlist_id

    def add_to_playlist(self, playlist_id: str, video_id: str, position: int = 0) -> bool:
        """영상을 플레이리스트에 추가"""
        try:
            self.service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        "position": position,
                    }
                },
            ).execute()
            logger.info("플레이리스트 추가 완료", video_id=video_id, playlist_id=playlist_id)
            return True
        except HttpError as e:
            logger.error("플레이리스트 추가 실패", error=str(e))
            return False

    def full_upload(
        self,
        upload: YouTubeUpload,
        seo: SEOMetadata,
        video_asset: VideoAsset,
        playlist_title: str = "Velvet Radio",
    ) -> dict[str, str]:
        """
        완전 업로드 파이프라인:
        1. 영상 업로드
        2. 썸네일 설정
        3. 채널 플레이리스트에 추가

        Returns:
            {video_id, video_url, playlist_id}
        """
        # 1. 업로드
        video_id = self.upload_video(upload)
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        # 2. 썸네일
        self.set_thumbnail(video_id, Path(video_asset.thumbnail_path))

        # 3. 플레이리스트 추가
        playlist_id = self.create_or_get_playlist(
            title=playlist_title,
            description=(
                f"Velvet Radio — Premium Easy-Listening Pop Playlists\n"
                f"New uploads every Tuesday & Friday 9AM KST\n"
                f"https://www.youtube.com/@VelvetRadio"
            ),
        )
        self.add_to_playlist(playlist_id, video_id)

        logger.info("전체 업로드 완료", video_id=video_id, url=video_url)
        return {
            "video_id": video_id,
            "video_url": video_url,
            "playlist_id": playlist_id,
        }
