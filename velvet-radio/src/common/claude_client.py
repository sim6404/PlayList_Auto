"""
Velvet Radio — Claude API 래퍼
모든 Phase에서 사용하는 Claude API 클라이언트
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import anthropic
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .config_loader import config
from .logger import get_logger

logger = get_logger(__name__)


class ClaudeClient:
    """Claude API 비동기 클라이언트 (재시도 + 구조화 로깅)"""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.model = config.claude_model
        logger.info("ClaudeClient initialized", model=self.model)

    @retry(
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
    )
    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        response_format: str = "text",  # "text" | "json"
    ) -> str:
        """기본 Chat Completion 호출"""
        logger.debug("Claude API call", max_tokens=max_tokens, format=response_format)

        messages = [{"role": "user", "content": user}]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
        )

        content = response.content[0].text
        logger.debug("Claude API response received", chars=len(content))
        return content

    def chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> dict | list:
        """JSON 응답 강제 파싱 (코드블록 자동 제거)"""
        raw = self.chat(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return self._parse_json(raw)

    def _parse_json(self, text: str) -> dict | list:
        """```json ... ``` 블록 또는 순수 JSON 파싱"""
        # 코드블록 제거
        cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("JSON 파싱 실패", raw=text[:200], error=str(e))
            # JSON 영역 추출 재시도
            match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Claude 응답에서 JSON을 추출할 수 없습니다: {e}") from e

    def generate_theme(self, system_prompt: str, channel_dna: dict) -> dict:
        """플레이리스트 테마 + 트랙리스트 생성"""
        user = (
            f"채널 DNA:\n{json.dumps(channel_dna, ensure_ascii=False, indent=2)}\n\n"
            "위 채널 정체성에 맞는 새 플레이리스트 테마와 20곡 트랙리스트를 JSON으로 생성해주세요."
        )
        return self.chat_json(
            system=system_prompt,
            user=user,
            max_tokens=4000,
            temperature=0.85,
        )

    def generate_lyrics(self, system_prompt: str, track: dict) -> str:
        """곡별 가사 생성"""
        user = (
            f"트랙 정보:\n{json.dumps(track, ensure_ascii=False, indent=2)}\n\n"
            "위 트랙에 맞는 가사를 Suno 메타태그 형식으로 작성해주세요."
        )
        return self.chat(
            system=system_prompt,
            user=user,
            max_tokens=1500,
            temperature=0.75,
        )

    def generate_seo(self, system_prompt: str, playlist: dict, tracks: list) -> dict:
        """다국어 SEO 메타데이터 생성"""
        user = (
            f"플레이리스트:\n{json.dumps(playlist, ensure_ascii=False, indent=2)}\n\n"
            f"트랙 목록:\n{json.dumps(tracks, ensure_ascii=False, indent=2)}\n\n"
            "YouTube 최적화를 위한 다국어 SEO 메타데이터를 JSON으로 생성해주세요."
        )
        return self.chat_json(
            system=system_prompt,
            user=user,
            max_tokens=3000,
            temperature=0.6,
        )


# 싱글턴 인스턴스
_claude_client: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeClient()
    return _claude_client
