"""
Velvet Radio — Phase 2: Suno API 클라이언트
서드파티 REST API를 통한 음악 생성 + 폴링 + 다운로드
"""
from __future__ import annotations

import asyncio
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
MAX_POLL_TIME = 600      # 10분
MAX_VARIANTS = 2         # 곡당 생성 변형 수


class SunoAPIError(Exception):
    """Suno API 에러"""


class SunoClient:
    """
    Suno 서드파티 API 클라이언트 (httpx 비동기)

    지원 API: SunoAPI.org / APIPASS / Apiframe
    모든 서비스가 유사한 OpenAI 호환 인터페이스를 제공함
    """

    def __init__(self):
        self.api_key = config.suno_api_key
        self.base_url = config.suno_api_base_url.rstrip("/")
        self.model = config.suno_model
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

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
            return r.json()

    async def generate(self, payload: SunoPayload) -> str:
        """
        곡 생성 요청

        Returns:
            job_id: 생성 작업 ID
        """
        data = {
            "prompt": payload.lyrics,
            "tags": payload.style_prompt,
            "title": payload.title or f"Track {payload.track_order}",
            "model": self.model,
            "make_instrumental": payload.instrumental,
            "wait_audio": False,   # 비동기 (폴링 방식)
        }

        logger.info("Suno 생성 요청", track=payload.track_order, title=payload.title)
        response = await self._post("/generate", data)

        job_id = response.get("id") or response.get("job_id")
        if not job_id:
            raise SunoAPIError(f"job_id가 없습니다: {response}")

        logger.info("Suno 생성 요청 완료", track=payload.track_order, job_id=job_id)
        return job_id

    async def poll_status(self, job_id: str) -> dict:
        """
        생성 완료까지 폴링

        Returns:
            완성된 트랙 정보 (audio_url 포함)
        """
        start = time.time()
        attempt = 0

        while True:
            elapsed = time.time() - start
            if elapsed > MAX_POLL_TIME:
                raise SunoAPIError(f"폴링 타임아웃 ({MAX_POLL_TIME}초): job_id={job_id}")

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(
                        f"{self.base_url}/feed/{job_id}",
                        headers=self.headers,
                    )
                    r.raise_for_status()
                    data = r.json()
            except Exception as e:
                logger.warning("폴링 실패, 재시도", job_id=job_id, error=str(e))
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # 응답 구조 정규화 (서비스마다 다름)
            items = data if isinstance(data, list) else [data]
            for item in items:
                status = item.get("status", "")
                if status == "complete":
                    logger.info("Suno 생성 완료", job_id=job_id, elapsed=f"{elapsed:.0f}s")
                    return item
                elif status in ("error", "failed"):
                    error_msg = item.get("error", "Unknown error")
                    raise SunoAPIError(f"생성 실패: {error_msg}")

            attempt += 1
            wait = min(POLL_INTERVAL * (1 + attempt * 0.3), 60)
            logger.debug("폴링 대기", job_id=job_id, elapsed=f"{elapsed:.0f}s", wait=f"{wait:.0f}s")
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
        """
        생성 → 폴링 → 다운로드 원스텝

        Returns:
            다운로드된 파일 경로 (실패 시 None)
        """
        out_dir = AUDIO_DIR / playlist_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"track_{payload.track_order:02d}_v{variant}.mp3"

        try:
            job_id = await self.generate(payload)
            track_data = await self.poll_status(job_id)
            audio_url = track_data.get("audio_url") or track_data.get("url")
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
    ) -> dict[int, list[Optional[Path]]]:
        """
        20곡 × 2변형 병렬 생성

        Returns:
            {track_order: [variant1_path, variant2_path]}
        """
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

        logger.info(
            "배치 생성 시작",
            total_tasks=len(tasks),
            concurrency=limit,
            payloads=len(payloads),
            variants=variants,
        )

        for coro in asyncio.as_completed(tasks):
            order, variant, path = await coro
            if order not in results:
                results[order] = []
            results[order].append(path)
            logger.info(
                "배치 진행",
                completed=sum(len(v) for v in results.values()),
                total=len(tasks),
            )

        return results
