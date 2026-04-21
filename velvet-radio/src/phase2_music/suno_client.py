"""
Velvet Radio — Phase 2: Suno API 클라이언트
SunoAPI.org v1 콜백 방식 — 생성 요청 → Vercel webhook 수신 → 로컬 폴링

흐름:
  1. POST /api/v1/generate (callBackUrl = Vercel /webhook/suno)
  2. taskId 반환
  3. Suno가 생성 완료 → Vercel /webhook/suno 로 콜백
  4. 로컬 워커가 Vercel /api/suno/result/{taskId} 폴링
  5. 결과 수신 → 음원 다운로드
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..common.config_loader import config
from ..common.logger import get_logger
from ..common.models import SunoPayload

logger = get_logger(__name__)

AUDIO_DIR = config.data_dir / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL = 30       # 초
MAX_POLL_TIME = 900      # 15분 (생성 시간 여유)
MAX_VARIANTS = 2         # 곡당 생성 변형 수

# Vercel 대시보드 URL (콜백 수신 + 결과 조회용)
DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL",
    "https://velvet-radio-dashboard.vercel.app"
).rstrip("/")


class SunoAPIError(Exception):
    """Suno API 에러"""


class SunoClient:
    """
    SunoAPI.org 클라이언트 — 콜백 기반 비동기 생성

    API 엔드포인트: https://api.sunoapi.org/api/v1/generate
    상태 폴링: Vercel 대시보드 /api/suno/result/{taskId}
    """

    def __init__(self):
        self.api_key = config.suno_api_key
        # 올바른 base_url: https://api.sunoapi.org/api/v1
        raw_url = config.suno_api_base_url.rstrip("/")
        # 이전 설정 (/v1) → 자동 교정
        if raw_url.endswith("/v1") and "api.sunoapi.org" in raw_url:
            self.base_url = raw_url.replace(
                "api.sunoapi.org/v1",
                "api.sunoapi.org/api/v1",
            )
        else:
            self.base_url = raw_url
        # 모델명 대문자 정규화 (v5 → V5)
        self.model = config.suno_model.upper()
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @property
    def _callback_url(self) -> str:
        return f"{DASHBOARD_URL}/webhook/suno"

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(config.max_retry_attempts),
    )
    async def _post(self, endpoint: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}{endpoint}",
                json=data,
                headers=self.headers,
            )
            r.raise_for_status()
            resp = r.json()
            # SunoAPI.org 응답 형식: {"code": 200, "msg": "success", "data": {...}}
            if isinstance(resp, dict) and resp.get("code") not in (200, None):
                raise SunoAPIError(f"API 오류 (code={resp.get('code')}): {resp.get('msg')}")
            return resp

    async def generate(self, payload: SunoPayload) -> str:
        """
        곡 생성 요청 → taskId 반환

        SunoAPI.org 필수 파라미터:
          customMode, prompt, tags, title, model, instrumental, callBackUrl
        """
        data = {
            "customMode": True,
            "prompt": payload.lyrics,
            "tags": payload.style_prompt,
            "title": payload.title or f"Track {payload.track_order}",
            "model": self.model,
            "instrumental": payload.instrumental,
            "callBackUrl": self._callback_url,
        }

        logger.info("Suno 생성 요청", track=payload.track_order, title=payload.title, model=self.model)
        response = await self._post("/generate", data)

        # taskId 추출 (data.taskId 또는 최상위 id/taskId)
        data_obj = response.get("data") or response
        task_id = (
            data_obj.get("taskId")
            or data_obj.get("task_id")
            or data_obj.get("id")
            or data_obj.get("job_id")
        )
        if not task_id:
            raise SunoAPIError(f"taskId가 없습니다: {response}")

        logger.info("Suno 생성 요청 완료", track=payload.track_order, task_id=task_id)
        return task_id

    async def poll_status(self, task_id: str) -> dict:
        """
        SunoAPI.org record-info 엔드포인트 직접 폴링

        실제 응답 구조:
          {"code":200, "data": {"taskId":"...", "response": {"sunoData": [
            {"id":"...", "audioUrl":"...", "sourceAudioUrl":"...", "duration":24.76, ...}
          ]}}}

        sunoData 항목에 audioUrl + duration 이 있으면 완료로 판정.
        """
        start = time.time()
        poll_url = f"{self.base_url}/generate/record-info"

        logger.info("Suno 결과 폴링 시작", task_id=task_id, url=poll_url)

        while True:
            elapsed = time.time() - start
            if elapsed > MAX_POLL_TIME:
                raise SunoAPIError(f"폴링 타임아웃 ({MAX_POLL_TIME}초): task_id={task_id}")

            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(poll_url, params={"taskId": task_id}, headers=self.headers)

                    if r.status_code == 429:
                        raise SunoAPIError("Suno 크레딧 부족 (429). api.sunoapi.org 크레딧 충전 필요")

                    if r.status_code == 200:
                        resp = r.json()
                        api_code = resp.get("code")

                        if api_code == 429:
                            raise SunoAPIError("Suno 크레딧 부족 (code=429). api.sunoapi.org 크레딧 충전 필요")

                        if api_code == 200:
                            data = resp.get("data") or {}
                            task_status = (data.get("status") or "").upper()

                            # 실패 상태 즉시 에러
                            if task_status in ("ERROR", "FAILED"):
                                err = data.get("errorMessage") or data.get("errorCode") or "Unknown"
                                raise SunoAPIError(f"생성 실패 (status={task_status}): {err}")

                            response_obj = data.get("response") or {}
                            suno_data = response_obj.get("sunoData") or []

                            if suno_data:
                                clip = suno_data[0]
                                audio_url = (
                                    clip.get("sourceAudioUrl")   # CDN 직접 URL 우선
                                    or clip.get("audioUrl")
                                    or clip.get("streamAudioUrl")
                                )
                                duration = clip.get("duration", 0)
                                if audio_url and duration:
                                    logger.info("Suno 생성 완료", task_id=task_id, elapsed=f"{elapsed:.0f}s", duration=duration)
                                    return {"status": "complete", "audio_url": audio_url}
                            # sunoData 없음 → 아직 생성 중 (PENDING/RUNNING)
                            logger.debug("Suno 생성 대기 중", task_id=task_id, task_status=task_status, elapsed=f"{elapsed:.0f}s")
                        else:
                            logger.warning("API 오류 코드", api_code=api_code, msg=resp.get("msg"), task_id=task_id)
                    else:
                        logger.warning("폴링 HTTP 오류", http_status=r.status_code, task_id=task_id)

            except SunoAPIError:
                raise
            except Exception as e:
                logger.warning("폴링 요청 실패", task_id=task_id, error=str(e))

            wait = min(POLL_INTERVAL * (1 + (elapsed / 300) * 0.5), 60)
            logger.debug("폴링 대기", task_id=task_id, elapsed=f"{elapsed:.0f}s", next_in=f"{wait:.0f}s")
            await asyncio.sleep(wait)

    async def download(self, audio_url: str, output_path: Path) -> Path:
        """음원 다운로드"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", audio_url) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

        size_kb = output_path.stat().st_size // 1024
        logger.info("음원 다운로드 완료", path=str(output_path), size_kb=size_kb)
        return output_path

    async def generate_and_download(
        self,
        payload: SunoPayload,
        playlist_id: str,
        variant: int = 1,
    ) -> Optional[Path]:
        """생성 → 폴링 → 다운로드 원스텝"""
        out_dir = AUDIO_DIR / playlist_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"track_{payload.track_order:02d}_v{variant}.mp3"

        try:
            task_id = await self.generate(payload)
            track_data = await self.poll_status(task_id)
            audio_url = (
                track_data.get("audio_url")
                or track_data.get("url")
                or track_data.get("audioUrl")
            )
            if not audio_url:
                raise SunoAPIError("audio_url이 없습니다")
            return await self.download(audio_url, out_path)
        except Exception as e:
            logger.error(
                "generate_and_download 실패",
                track=payload.track_order,
                variant=variant,
                error=str(e),
            )
            return None

    async def generate_batch(
        self,
        payloads: list[SunoPayload],
        playlist_id: str,
        variants: int = MAX_VARIANTS,
        concurrency: int = None,
        progress_cb=None,
    ) -> dict[int, list[Optional[Path]]]:
        """20곡 × 2변형 병렬 생성"""
        limit = concurrency or config.max_concurrent_suno_jobs
        semaphore = asyncio.Semaphore(limit)
        results: dict[int, list[Optional[Path]]] = {}

        async def bounded_generate(p: SunoPayload, v: int) -> tuple[int, int, Optional[Path]]:
            async with semaphore:
                path = await self.generate_and_download(p, playlist_id, variant=v)
                return p.track_order, v, path

        tasks = [
            bounded_generate(p, v + 1)
            for p in payloads
            for v in range(variants)
        ]
        total = len(tasks)

        logger.info(
            "배치 생성 시작",
            total_tasks=total,
            concurrency=limit,
            payloads=len(payloads),
            variants=variants,
        )

        for coro in asyncio.as_completed(tasks):
            order, variant, path = await coro
            if order not in results:
                results[order] = []
            results[order].append(path)
            completed = sum(len(v) for v in results.values())
            logger.info("배치 진행", completed=completed, total=total)
            if progress_cb:
                try:
                    await progress_cb(completed, total)
                except Exception:
                    pass

        return results
