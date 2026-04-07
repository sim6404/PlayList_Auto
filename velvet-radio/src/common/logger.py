"""
Velvet Radio — 구조화 로깅 (structlog)
JSON Lines 형식으로 ./logs/ 에 저장
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import os

import structlog


LOG_DIR = Path(os.environ.get("LOG_DIR", str(Path(__file__).parent.parent.parent / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _configure_logging() -> None:
    """structlog + 표준 logging 통합 설정"""
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        foreign_pre_chain=shared_processors,
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    # File handler (일별 로테이션)
    from logging.handlers import TimedRotatingFileHandler
    file_handler = TimedRotatingFileHandler(
        LOG_DIR / "velvet_radio.jsonl",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.addHandler(console)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)

    # 외부 라이브러리 노이즈 억제
    for noisy in ("urllib3", "httpx", "httpcore", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configured = False


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    global _configured
    if not _configured:
        _configure_logging()
        _configured = True
    return structlog.get_logger(name)
