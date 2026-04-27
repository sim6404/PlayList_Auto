"""
Velvet Radio — Phase 4: 마스터 승인 관리자
Telegram Bot 콜백 + Dashboard API 연동으로 이중 승인 채널 제공
업로드는 마스터 승인 후에만 실행
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import (
    ApprovalRequest,
    ApprovalStatus,
    QualityReport,
    SEOMetadata,
    VideoAsset,
)
from ..common.notifier import get_notifier

logger = get_logger(__name__)

PENDING_DIR = config.data_dir / "pending_approval"
APPROVED_DIR = config.data_dir / "approved"
PENDING_DIR.mkdir(parents=True, exist_ok=True)
APPROVED_DIR.mkdir(parents=True, exist_ok=True)

# 마스터 승인 대기 최대 시간 (48시간)
APPROVAL_TIMEOUT_HOURS = 48
POLL_INTERVAL_SECONDS = 30


class ApprovalManager:
    """
    이중 승인 채널:
    1. Telegram 인라인 버튼 (모바일 빠른 승인)
    2. 웹 대시보드 (상세 검토 후 승인)
    """

    def __init__(self):
        self.notifier = get_notifier()

    def submit_for_approval(
        self,
        playlist_id: str,
        video_asset: VideoAsset,
        seo: SEOMetadata,
        quality_report: QualityReport,
    ) -> ApprovalRequest:
        """
        마스터 승인 요청 제출

        1. ApprovalRequest 생성 + 파일 저장 (배경 샘플 5종 포함)
        2. Telegram 알림 발송 (썸네일 + 버튼)
        3. 승인 대기 시작

        Returns:
            ApprovalRequest
        """
        req = ApprovalRequest(
            playlist_id=playlist_id,
            video_path=video_asset.final_video_path,
            thumbnail_path=video_asset.thumbnail_path,
            background_samples=video_asset.background_samples,
            selected_background_index=0,  # 기본: sample_1
            seo=seo,
            quality_report=quality_report,
            status=ApprovalStatus.PENDING,
        )

        self._save_request(req)
        logger.info("승인 요청 제출", playlist_id=playlist_id)

        # Telegram 알림
        self.notifier.request_approval(req)

        return req

    def _save_request(self, req: ApprovalRequest) -> None:
        """승인 요청을 pending_approval/ 에 저장"""
        path = PENDING_DIR / f"{req.playlist_id}.json"
        path.write_text(req.model_dump_json(indent=2), encoding="utf-8")

    def _load_request(self, playlist_id: str) -> Optional[ApprovalRequest]:
        """저장된 승인 요청 로드"""
        path = PENDING_DIR / f"{playlist_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ApprovalRequest(**data)
        except Exception as e:
            logger.error("승인 요청 로드 실패", error=str(e))
            return None

    def get_status(self, playlist_id: str) -> ApprovalStatus:
        """현재 승인 상태 조회"""
        req = self._load_request(playlist_id)
        if not req:
            return ApprovalStatus.PENDING
        return req.status

    def approve(self, playlist_id: str, feedback: str = "") -> bool:
        """마스터 승인 처리"""
        req = self._load_request(playlist_id)
        if not req:
            logger.error("승인할 요청 없음", playlist_id=playlist_id)
            return False

        req.status = ApprovalStatus.APPROVED
        req.master_feedback = feedback
        req.reviewed_at = datetime.utcnow().isoformat()

        # pending → approved 이동
        self._save_request(req)
        approved_path = APPROVED_DIR / f"{playlist_id}.json"
        approved_path.write_text(req.model_dump_json(indent=2), encoding="utf-8")
        pending_path = PENDING_DIR / f"{playlist_id}.json"
        if pending_path.exists():
            pending_path.unlink()

        logger.info("마스터 승인 완료", playlist_id=playlist_id)
        return True

    def reject(self, playlist_id: str, reason: str = "") -> bool:
        """마스터 거절 처리"""
        req = self._load_request(playlist_id)
        if not req:
            return False

        req.status = ApprovalStatus.REJECTED
        req.master_feedback = reason
        req.reviewed_at = datetime.utcnow().isoformat()
        self._save_request(req)

        self.notifier.notify_rejected(playlist_id, reason)
        logger.info("마스터 거절", playlist_id=playlist_id, reason=reason)
        return True

    def request_revision(self, playlist_id: str, feedback: str) -> bool:
        """수정 요청"""
        req = self._load_request(playlist_id)
        if not req:
            return False

        req.status = ApprovalStatus.REVISION
        req.master_feedback = feedback
        req.reviewed_at = datetime.utcnow().isoformat()
        self._save_request(req)

        self.notifier._send(
            f"🔁 <b>[Velvet Radio] 수정 요청</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📎 {playlist_id}\n"
            f"📝 피드백: {feedback}\n"
            f"파이프라인 재실행이 필요합니다."
        )
        logger.info("수정 요청", playlist_id=playlist_id, feedback=feedback)
        return True

    def wait_for_approval(
        self,
        playlist_id: str,
        timeout_hours: float = APPROVAL_TIMEOUT_HOURS,
    ) -> ApprovalStatus:
        """
        마스터 승인/거절까지 블로킹 대기

        Args:
            playlist_id: 플레이리스트 ID
            timeout_hours: 최대 대기 시간 (시간)

        Returns:
            ApprovalStatus (approved / rejected / revision_requested)
        """
        deadline = time.time() + timeout_hours * 3600
        logger.info(
            "승인 대기 시작",
            playlist_id=playlist_id,
            timeout_hours=timeout_hours,
        )

        while time.time() < deadline:
            status = self.get_status(playlist_id)
            if status != ApprovalStatus.PENDING:
                logger.info("승인 결정 수신", playlist_id=playlist_id, status=status)
                return status

            elapsed_h = (time.time() - (deadline - timeout_hours * 3600)) / 3600
            if elapsed_h > 0 and int(elapsed_h) % 6 == 0:
                # 6시간마다 리마인더
                remaining_h = (deadline - time.time()) / 3600
                self.notifier._send(
                    f"⏰ <b>[Velvet Radio] 승인 대기 중</b>\n"
                    f"📎 {playlist_id}\n"
                    f"남은 시간: {remaining_h:.0f}시간"
                )

            time.sleep(POLL_INTERVAL_SECONDS)

        logger.warning("승인 타임아웃", playlist_id=playlist_id)
        self.reject(playlist_id, f"타임아웃: {timeout_hours}시간 내 응답 없음")
        return ApprovalStatus.REJECTED

    def list_pending(self) -> list[dict]:
        """대기 중인 승인 요청 목록"""
        pending = []
        for f in sorted(PENDING_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                pending.append({
                    "playlist_id": data.get("playlist_id"),
                    "title": data.get("seo", {}).get("title_en", ""),
                    "requested_at": data.get("requested_at"),
                    "status": data.get("status"),
                })
            except Exception:
                continue
        return pending

    def handle_telegram_callback(self, callback_data: str) -> bool:
        """
        Telegram 인라인 버튼 콜백 처리
        callback_data 형식: "approve:playlist_id" | "reject:playlist_id" | "revision:playlist_id"
        """
        try:
            action, playlist_id = callback_data.split(":", 1)
        except ValueError:
            logger.error("잘못된 콜백 형식", data=callback_data)
            return False

        if action == "approve":
            return self.approve(playlist_id)
        elif action == "reject":
            return self.reject(playlist_id, "마스터 거절 (Telegram)")
        elif action == "revision":
            return self.request_revision(playlist_id, "Telegram에서 수정 요청")
        else:
            logger.warning("알 수 없는 액션", action=action)
            return False
