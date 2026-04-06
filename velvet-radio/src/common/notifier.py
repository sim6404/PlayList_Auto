"""
Velvet Radio — 알림 모듈 (Telegram)
파이프라인 상태, 에러, 마스터 승인 요청 발송
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from .config_loader import config
from .logger import get_logger
from .models import ApprovalRequest, PipelineRun, QualityReport

logger = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """Telegram Bot API 래퍼"""

    def __init__(self):
        self.token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self.base_url = f"{TELEGRAM_API}/bot{self.token}"

    def _send(self, text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
        """동기 전송 (n8n 워커에서 호출)"""
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": disable_preview,
                    },
                )
                r.raise_for_status()
                logger.info("Telegram 메시지 전송 완료", chars=len(text))
                return True
        except Exception as e:
            logger.error("Telegram 전송 실패", error=str(e))
            return False

    def _send_photo(self, photo_path: str, caption: str) -> bool:
        """썸네일 이미지 전송"""
        try:
            with httpx.Client(timeout=30) as client:
                with open(photo_path, "rb") as f:
                    r = client.post(
                        f"{self.base_url}/sendPhoto",
                        data={"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"},
                        files={"photo": f},
                    )
                r.raise_for_status()
                return True
        except Exception as e:
            logger.error("Telegram 사진 전송 실패", error=str(e))
            return False

    def _send_inline_keyboard(self, text: str, keyboard: list) -> bool:
        """인라인 키보드 (승인/거절 버튼)"""
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": keyboard},
                    },
                )
                r.raise_for_status()
                return True
        except Exception as e:
            logger.error("Telegram 키보드 전송 실패", error=str(e))
            return False

    # ── 파이프라인 알림 ──────────────────────────────

    def notify_pipeline_start(self, playlist_id: str, theme: str) -> None:
        msg = (
            "🎬 <b>[Velvet Radio] 파이프라인 시작</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"📎 <b>테마:</b> {theme}\n"
            f"🆔 <b>ID:</b> <code>{playlist_id}</code>\n"
            "⏳ Phase 1 (컨셉 기획) 진행 중..."
        )
        self._send(msg)

    def notify_phase_complete(self, phase: str, summary: str) -> None:
        icons = {"phase1": "📝", "phase2": "🎵", "phase3": "🎬", "phase4": "📤"}
        icon = icons.get(phase, "✅")
        msg = (
            f"{icon} <b>[Velvet Radio] {phase.upper()} 완료</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"{summary}"
        )
        self._send(msg)

    def notify_error(self, phase: str, error: str, track_info: Optional[str] = None) -> None:
        detail = f"\n📍 <b>트랙:</b> {track_info}" if track_info else ""
        msg = (
            f"🚨 <b>[Velvet Radio] {phase.upper()} 에러</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"❌ {error}{detail}\n"
            "🔄 재시도 중 또는 건너뜀..."
        )
        self._send(msg)

    def notify_pipeline_complete(self, run: PipelineRun, youtube_url: str) -> None:
        phases = {p.phase: p for p in run.phases}
        durations = " | ".join(
            f"{k}: {v.duration_seconds:.0f}s"
            for k, v in phases.items()
        )
        msg = (
            "✅ <b>[Velvet Radio] 파이프라인 완료!</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"⏱ {durations}\n"
            f"🔗 <a href='{youtube_url}'>YouTube 영상 보기</a>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⏳ 마스터 승인 대기 중..."
        )
        self._send(msg)

    # ── 마스터 승인 요청 ──────────────────────────────

    def request_approval(self, req: ApprovalRequest) -> None:
        """마스터에게 승인 요청 — 인라인 버튼 포함"""
        dashboard_url = f"http://localhost:{config.dashboard_port}/review/{req.playlist_id}"

        # 썸네일 전송 (있으면)
        try:
            self._send_photo(
                req.thumbnail_path,
                caption=f"📸 <b>{req.seo.title_en}</b>",
            )
        except Exception:
            pass

        quality = req.quality_report
        text = (
            "🎯 <b>[Velvet Radio] 마스터 승인 요청</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"📎 <b>제목:</b> {req.seo.title_en}\n"
            f"🎵 <b>선별 곡 수:</b> {quality.selected_count}/{quality.total_generated}곡\n"
            f"⚠️ <b>실패 트랙:</b> {quality.failed_tracks or '없음'}\n"
            f"\n🌐 <b>대시보드 검토:</b>\n"
            f"<a href='{dashboard_url}'>{dashboard_url}</a>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "아래 버튼 또는 대시보드에서 승인/거절하세요."
        )

        keyboard = [
            [
                {"text": "✅ 승인", "callback_data": f"approve:{req.playlist_id}"},
                {"text": "❌ 거절", "callback_data": f"reject:{req.playlist_id}"},
            ],
            [{"text": "🔁 수정 요청", "callback_data": f"revision:{req.playlist_id}"}],
        ]
        self._send_inline_keyboard(text, keyboard)

    def notify_approved(self, playlist_id: str, youtube_url: str) -> None:
        msg = (
            "🚀 <b>[Velvet Radio] 업로드 완료!</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"✅ 마스터 승인 → YouTube 업로드 성공\n"
            f"🔗 <a href='{youtube_url}'>영상 보기</a>"
        )
        self._send(msg)

    def notify_rejected(self, playlist_id: str, reason: str) -> None:
        msg = (
            "🗑 <b>[Velvet Radio] 업로드 취소</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"🆔 {playlist_id}\n"
            f"📝 사유: {reason}"
        )
        self._send(msg)


# 싱글턴
_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
