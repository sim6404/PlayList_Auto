"""
Velvet Radio — 로컬 파이프라인 워커
수동 실행 또는 트리거 파일/Telegram 감지 시 전체 파이프라인 실행

Usage:
    python worker.py --run-now               # 즉시 실행
    python worker.py --watch                 # 감시 모드 (트리거 대기)
    python worker.py --watch --interval 30   # 30초 간격 감시
    python worker.py --status                # 현재 상태 확인
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 경로 설정 ──────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# .env 로드 (src/ 임포트 전에 필요)
from dotenv import load_dotenv

load_dotenv(ROOT / "config" / ".env", override=True)
load_dotenv(ROOT / ".env", override=False)

# ── 필수 패키지 확인 ────────────────────────────────────────────────
try:
    import httpx
except ImportError:
    print("[Worker] [!]  httpx 패키지가 없습니다. pip install httpx 실행 후 재시도하세요.")
    sys.exit(1)

# ── src/ 모듈 임포트 ────────────────────────────────────────────────
try:
    from src.common.config_loader import config
    from src.common.logger import get_logger
    from src.phase4_publish.scheduler import (
        run_full_pipeline,
        run_phase1_only,
        run_phase2_only,
        run_phase3_only,
        run_phase4_only,
    )
    PIPELINE_AVAILABLE = True
except ImportError as e:
    print(f"[Worker] [!]  파이프라인 모듈 로드 실패: {e}")
    print("[Worker]    requirements-full.txt 의존성을 설치하세요:")
    print("[Worker]    pip install -r requirements-full.txt")
    PIPELINE_AVAILABLE = False

# ── 경로 상수 ──────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
RUN_QUEUE_DIR = DATA_DIR / "run_queue"
STATUS_FILE = DATA_DIR / "pipeline_status.json"

RUN_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_API = "https://api.telegram.org"

# ── 로거 ───────────────────────────────────────────────────────────
if PIPELINE_AVAILABLE:
    logger = get_logger(__name__)
else:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)


# ── 상태 관리 ──────────────────────────────────────────────────────

def write_status(status: str, run_id: str = "", details: dict | None = None) -> None:
    """data/pipeline_status.json 업데이트"""
    state = {
        "status": status,      # idle | running | completed | failed
        "run_id": run_id,
        "updated_at": datetime.utcnow().isoformat(),
        **(details or {}),
    }
    try:
        STATUS_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"[Worker] 상태 파일 쓰기 실패: {e}")


def read_status() -> dict:
    """현재 파이프라인 상태 읽기"""
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"status": "idle", "updated_at": ""}


def print_status() -> None:
    """현재 상태를 콘솔에 출력"""
    s = read_status()
    status = s.get("status", "idle")
    icons = {"idle": "[-]", "running": "[~]", "completed": "[OK]", "failed": "[X]"}
    print(f"\n{icons.get(status, '?')} 파이프라인 상태: {status.upper()}")
    if s.get("run_id"):
        print(f"   Run ID    : {s['run_id']}")
    if s.get("updated_at"):
        print(f"   업데이트   : {s['updated_at'][:19].replace('T', ' ')} UTC")
    if s.get("youtube_url"):
        print(f"   YouTube   : {s['youtube_url']}")
    if s.get("error"):
        print(f"   오류      : {s['error']}")
    if s.get("phases"):
        print("   단계      :", " → ".join(
            f"{p['phase']}({p['status']})" for p in s["phases"]
        ))
    print()


# ── 트리거 파일 ────────────────────────────────────────────────────

def pick_trigger() -> dict | None:
    """
    run_queue/ 에서 가장 오래된 트리거 파일 선택 후 삭제
    Returns trigger dict or None
    """
    triggers = sorted(
        RUN_QUEUE_DIR.glob("trigger_*.json"),
        key=lambda x: x.stat().st_mtime,
    )
    for path in triggers:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            path.unlink()
            print(f"[Worker] 트리거 감지: {path.name}")
            return data
        except Exception:
            pass
    return None


def write_trigger(run_id: str, source: str = "manual") -> Path:
    """트리거 파일 생성 (테스트 및 원격 호출용)"""
    data = {
        "run_id": run_id,
        "source": source,
        "requested_at": datetime.utcnow().isoformat(),
    }
    path = RUN_QUEUE_DIR / f"trigger_{run_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ── Telegram 폴링 ──────────────────────────────────────────────────

class TelegramPoller:
    """
    Telegram getUpdates 로 /run 커맨드 감지
    봇에게 /run 을 보내면 워커가 파이프라인을 시작합니다
    """

    def __init__(self, bot_token: str, admin_chat_id: str):
        self.bot_token = bot_token
        self.admin_chat_id = str(admin_chat_id)
        self.base_url = f"{TELEGRAM_API}/bot{bot_token}"
        self._last_update_id: int | None = None

    def poll(self) -> bool:
        """
        최신 업데이트에서 /run 커맨드 감지
        Returns True if /run command found from admin
        """
        if not self.bot_token or not self.admin_chat_id:
            return False
        try:
            params = {"timeout": 1, "allowed_updates": ["message"]}
            if self._last_update_id is not None:
                params["offset"] = self._last_update_id + 1

            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self.base_url}/getUpdates", params=params)
                data = resp.json()

            updates = data.get("result", [])
            if not updates:
                return False

            # 마지막 update_id 저장
            self._last_update_id = max(u["update_id"] for u in updates)

            for update in updates:
                msg = update.get("message", {})
                text = (msg.get("text") or "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                # 관리자 채팅에서 /run 명령
                if chat_id == self.admin_chat_id and text in ["/run", "/run@velvetradiobot"]:
                    print(f"[Worker] Telegram /run 명령 감지 (chat: {chat_id})")
                    return True

        except Exception as e:
            print(f"[Worker] Telegram 폴링 오류: {e}")
        return False

    def send(self, text: str) -> None:
        """관리자에게 메시지 전송"""
        if not self.bot_token or not self.admin_chat_id:
            return
        try:
            with httpx.Client(timeout=10) as client:
                client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.admin_chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
        except Exception as e:
            print(f"[Worker] Telegram 전송 실패: {e}")


# ── 파이프라인 실행 ────────────────────────────────────────────────

async def execute_pipeline(trigger_data: dict, tg: TelegramPoller | None = None) -> None:
    """
    전체 파이프라인 실행 + 상태 관리 + Telegram 알림
    """
    if not PIPELINE_AVAILABLE:
        print("[Worker] [X] 파이프라인 모듈을 사용할 수 없습니다. 의존성을 설치하세요.")
        return

    run_id = trigger_data.get("run_id", datetime.utcnow().strftime("%Y%m%d_%H%M%S"))
    source = trigger_data.get("source", "unknown")

    print(f"\n{'='*50}")
    print(f"[Worker] >> 파이프라인 시작")
    print(f"[Worker]    Run ID : {run_id}")
    print(f"[Worker]    출처   : {source}")
    print(f"[Worker]    시작   : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*50}\n")

    write_status("running", run_id=run_id, details={
        "started_at": datetime.utcnow().isoformat(),
        "source": source,
    })

    if tg:
        tg.send(
            f">> <b>[Velvet Radio] 파이프라인 시작</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🆔 Run ID: <code>{run_id}</code>\n"
            f"📍 출처: {source}\n"
            f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"Phase 1→2→3→4 순서로 진행됩니다..."
        )

    try:
        result = await run_full_pipeline()

        phase_summary = [
            {
                "phase": p.phase,
                "status": p.status.value,
                "duration": round(p.duration_seconds, 1),
                "summary": p.output_summary or "",
            }
            for p in result.phases
        ]

        # overall_status에 따라 "completed" vs "failed" 정확히 기록
        final_status = "completed" if result.overall_status.value == "completed" else "failed"
        write_status(
            final_status,
            run_id=run_id,
            details={
                "completed_at": datetime.utcnow().isoformat(),
                "youtube_url": result.youtube_url or "",
                "overall_status": result.overall_status.value,
                "phases": phase_summary,
            },
        )

        print(f"\n{'='*50}")
        print(f"[Worker] [OK] 파이프라인 완료: {result.overall_status.value}")
        if result.youtube_url:
            print(f"[Worker]    YouTube: {result.youtube_url}")
        print(f"{'='*50}\n")

        if tg:
            phases_str = "\n".join(
                f"  {'[OK]' if p['status']=='completed' else '[X]'} {p['phase']}: {p['duration']}s"
                for p in phase_summary
            )
            tg.send(
                f"[OK] <b>[Velvet Radio] 파이프라인 완료!</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{phases_str}\n"
                + (f"🔗 <a href='{result.youtube_url}'>YouTube 영상</a>" if result.youtube_url else "[~] 승인 대기 중")
            )

    except Exception as e:
        error_msg = str(e)
        print(f"\n[Worker] [X] 파이프라인 실패: {error_msg}")

        write_status("failed", run_id=run_id, details={
            "error": error_msg,
            "failed_at": datetime.utcnow().isoformat(),
        })

        if tg:
            tg.send(
                f"💥 <b>[Velvet Radio] 파이프라인 실패</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"🆔 Run ID: <code>{run_id}</code>\n"
                f"[X] {error_msg[:500]}"
            )


# ── Phase 단독 실행 ────────────────────────────────────────────────

async def execute_phase(phase_num: int, session_id: str, trigger_data: dict,
                        tg: TelegramPoller | None = None) -> None:
    """특정 Phase만 단독 실행 (Pipeline Studio용)"""
    if not PIPELINE_AVAILABLE:
        print("[Worker] [X] 파이프라인 모듈을 사용할 수 없습니다.")
        return

    run_id = trigger_data.get("run_id", datetime.utcnow().strftime("%Y%m%d_%H%M%S"))

    print(f"\n{'='*50}")
    print(f"[Worker] >> Phase {phase_num} 단독 실행")
    print(f"[Worker]    Session : {session_id}")
    print(f"[Worker]    시작    : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*50}\n")

    write_status("running", run_id=run_id, details={
        "started_at": datetime.utcnow().isoformat(),
        "phase": phase_num,
        "session_id": session_id,
        "source": trigger_data.get("source", "studio"),
    })

    phase_runners = {
        1: run_phase1_only,
        2: run_phase2_only,
        3: run_phase3_only,
        4: run_phase4_only,
    }
    runner = phase_runners.get(phase_num)
    if runner is None:
        print(f"[Worker] [X] 잘못된 Phase 번호: {phase_num}")
        write_status("failed", run_id=run_id, details={"error": f"Invalid phase: {phase_num}"})
        return

    try:
        if phase_num == 1:
            result = await runner(session_id)
        else:
            result = await runner(session_id)

        phase_status = result.get("phases", {}).get(f"phase{phase_num}", {}).get("status", "?")
        print(f"\n[Worker] [OK] Phase {phase_num} 완료: {phase_status}")

        write_status("completed", run_id=run_id, details={
            "completed_at": datetime.utcnow().isoformat(),
            "phase": phase_num,
            "session_id": session_id,
            "phase_status": phase_status,
        })

        if tg:
            summary = result.get("phases", {}).get(f"phase{phase_num}", {}).get("summary", "")
            tg.send(
                f"[OK] <b>[Studio] Phase {phase_num} 완료</b>\n"
                f"Session: <code>{session_id}</code>\n"
                f"{summary}"
            )

    except Exception as e:
        error_msg = str(e)
        print(f"\n[Worker] [X] Phase {phase_num} 실패: {error_msg}")
        write_status("failed", run_id=run_id, details={
            "error": error_msg,
            "failed_at": datetime.utcnow().isoformat(),
            "phase": phase_num,
            "session_id": session_id,
        })
        if tg:
            tg.send(
                f"[X] <b>[Studio] Phase {phase_num} 실패</b>\n"
                f"Session: <code>{session_id}</code>\n"
                f"{error_msg[:300]}"
            )


# ── 감시 루프 ──────────────────────────────────────────────────────

def watch_loop(interval: int = 60) -> None:
    """
    감시 모드 — 두 가지 트리거 방식:
    1. data/run_queue/trigger_*.json 파일 감지
    2. Telegram 봇에 /run 명령 전송
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    tg = TelegramPoller(bot_token, chat_id) if (bot_token and chat_id) else None

    print(f"\n{'='*50}")
    print(f"[Worker] [*]  감시 모드 시작")
    print(f"[Worker]    감시 간격 : {interval}초")
    print(f"[Worker]    트리거 폴더: {RUN_QUEUE_DIR}")
    print(f"[Worker]    Telegram : {'활성' if tg else '비활성 (TOKEN/CHAT_ID 없음)'}")
    if tg:
        print(f"[Worker]    → 봇에게 /run 을 보내면 파이프라인이 시작됩니다")
    print(f"[Worker]    Ctrl+C 로 종료")
    print(f"{'='*50}\n")

    if tg:
        tg.send(
            "[*] <b>[Velvet Radio] 워커 시작됨</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"감시 간격: {interval}초\n"
            "트리거: 대시보드 '▶️ 지금 실행' 또는 /run 명령"
        )

    write_status("idle")

    try:
        while True:
            # 현재 실행 중이면 skip
            current = read_status()
            if current.get("status") == "running":
                print(f"[Worker] [~] 실행 중... (run_id: {current.get('run_id', '?')})")
                time.sleep(interval)
                continue

            # 1. 트리거 파일 우선 확인
            trigger = pick_trigger()
            if trigger:
                # Phase 단독 실행 트리거인지 확인
                if "phase" in trigger and "session_id" in trigger:
                    asyncio.run(execute_phase(
                        trigger["phase"],
                        trigger["session_id"],
                        trigger,
                        tg,
                    ))
                else:
                    asyncio.run(execute_pipeline(trigger, tg))
                continue

            # 2. Telegram /run 명령 확인
            if tg and tg.poll():
                run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                asyncio.run(execute_pipeline(
                    {"run_id": run_id, "source": "telegram"},
                    tg,
                ))
                continue

            # 대기
            now = datetime.utcnow().strftime("%H:%M:%S")
            print(f"[Worker] [{now}] 대기 중... (다음 확인: {interval}초 후)")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n[Worker] [bye] 워커 종료됨")
        write_status("idle")
        if tg:
            tg.send("[bye] <b>[Velvet Radio] 워커 종료됨</b>")


# ── 메인 ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Velvet Radio - 파이프라인 로컬 워커",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python worker.py --run-now                           즉시 파이프라인 실행
  python worker.py --watch                             감시 모드 (60초 간격)
  python worker.py --watch --interval 30               감시 모드 (30초 간격)
  python worker.py --status                            현재 상태 확인
  python worker.py --phase 3 --session 20260421_120000 Phase 3만 단독 실행
  python worker.py --resume 20260421_120000            마지막 성공 Phase 이후부터 재실행
  python worker.py --resume 20260421_120000 --from-phase 3  Phase 3부터 강제 재실행
        """,
    )
    parser.add_argument("--run-now", action="store_true", help="즉시 파이프라인 실행")
    parser.add_argument("--watch", action="store_true", help="감시 모드 (트리거 대기)")
    parser.add_argument("--interval", type=int, default=60, help="감시 간격(초, 기본: 60)")
    parser.add_argument("--status", action="store_true", help="현재 상태 확인 후 종료")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4], help="특정 Phase만 실행 (1~4)")
    parser.add_argument("--session", type=str, default="", help="세션 ID (--phase와 함께 사용)")
    parser.add_argument("--resume", type=str, default="", metavar="SESSION_ID",
                        help="기존 세션을 불러와 마지막 성공 Phase 이후부터 재실행")
    parser.add_argument("--from-phase", type=int, choices=[1, 2, 3, 4], dest="from_phase",
                        help="--resume 시 이 Phase부터 강제 재실행 (기본: 자동 감지)")

    args = parser.parse_args()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    tg = TelegramPoller(bot_token, chat_id) if (bot_token and chat_id) else None

    if args.status:
        print_status()

    elif args.phase:
        session_id = args.session or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        print(f"[Worker] >> Phase {args.phase} 단독 실행 (session: {session_id})")
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + f"_p{args.phase}"
        asyncio.run(execute_phase(
            args.phase,
            session_id,
            {"run_id": run_id, "source": "cli_phase"},
            tg,
        ))

    elif args.resume:
        session_id = args.resume
        # 마지막 성공 Phase 자동 감지
        if PIPELINE_AVAILABLE:
            from src.phase4_publish.scheduler import load_session as _load_sess
            try:
                sess_data = _load_sess(session_id)
                phases = sess_data.get("phases", {})
                _phase_order = [1, 2, 3, 4]
                if args.from_phase:
                    start_phase = args.from_phase
                else:
                    # 마지막으로 완료된 Phase 다음부터
                    last_ok = 0
                    for n in _phase_order:
                        if phases.get(f"phase{n}", {}).get("status") == "completed":
                            last_ok = n
                    start_phase = last_ok + 1 if last_ok < 4 else 4

                print(f"[Worker] >> 세션 재개: {session_id} (Phase {start_phase}부터)")
                for phase_num in range(start_phase, 5):
                    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + f"_p{phase_num}"
                    asyncio.run(execute_phase(
                        phase_num,
                        session_id,
                        {"run_id": run_id, "source": "cli_resume"},
                        tg,
                    ))
                    # 방금 실행한 Phase 결과 확인 — 실패하면 중단
                    sess_data = _load_sess(session_id)
                    ph_status = sess_data.get("phases", {}).get(f"phase{phase_num}", {}).get("status", "")
                    if ph_status != "completed":
                        print(f"[Worker] [X] Phase {phase_num} 실패 — 재개 중단")
                        break
            except FileNotFoundError:
                print(f"[Worker] [X] 세션을 찾을 수 없습니다: {session_id}")
                sys.exit(1)
        else:
            print("[Worker] [X] 파이프라인 모듈을 사용할 수 없습니다. 의존성을 설치하세요.")
            sys.exit(1)

    elif args.run_now:
        print("[Worker] >> 즉시 실행 모드")
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        asyncio.run(execute_pipeline(
            {"run_id": run_id, "source": "cli_run_now"},
            tg,
        ))

    elif args.watch:
        watch_loop(args.interval)

    else:
        parser.print_help()
        print("\n[!] --run-now / --watch / --phase N / --status 중 하나를 지정하세요")


if __name__ == "__main__":
    main()
