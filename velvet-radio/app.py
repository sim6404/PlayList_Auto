"""
Velvet Radio — Vercel 서버리스 진입점 (독립형)
src/ 임포트 없이 Flask + 환경변수만 사용
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

# ── 경로 설정 ──────────────────────────────────────────────────────
ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "dashboard" / "templates"
STATIC_DIR = ROOT / "dashboard" / "static"

DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
PENDING_DIR = DATA_DIR / "pending_approval"
APPROVED_DIR = DATA_DIR / "approved"
RUN_QUEUE_DIR = DATA_DIR / "run_queue"
STATUS_FILE = DATA_DIR / "pipeline_status.json"
SUNO_CALLBACKS_DIR = DATA_DIR / "suno_callbacks"
SESSION_DIR = DATA_DIR / "sessions"
PLAYLISTS_DIR = DATA_DIR / "playlists"

for d in [PENDING_DIR, APPROVED_DIR, RUN_QUEUE_DIR, SUNO_CALLBACKS_DIR, SESSION_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Flask 앱 ──────────────────────────────────────────────────────
app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get("DASHBOARD_SECRET_KEY", "velvet-radio-dev-secret")

MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "velvet2024")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
N8N_WEBHOOK_BASE_URL = os.environ.get("N8N_WEBHOOK_BASE_URL", "")
IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


@app.context_processor
def inject_globals():
    return {"is_vercel": IS_VERCEL}


def _vercel_local_only():
    return jsonify({
        "error": "local_only",
        "message": "Pipeline Studio는 로컬 환경에서만 사용할 수 있습니다.",
        "hint": "로컬 실행: python -m dashboard.app  →  http://localhost:8080/pipeline-studio",
    }), 503


# ── 인증 ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Telegram 헬퍼 ─────────────────────────────────────────────────

def _tg_send(text: str, keyboard: list | None = None) -> bool:
    """Telegram Bot API로 메시지 전송 (urllib 사용, 외부 의존성 없음)"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        payload: dict = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        return True
    except Exception:
        return False


def _tg_send_run_request(run_id: str) -> None:
    """파이프라인 실행 요청 Telegram 알림"""
    dashboard_url = f"https://{os.environ.get('VERCEL_URL', 'velvet-radio-dashboard.vercel.app')}"
    text = (
        "🚀 <b>[Velvet Radio] 파이프라인 수동 실행 요청</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🆔 Run ID: <code>{run_id}</code>\n"
        f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n"
        "━━━━━━━━━━━━━━━━━\n"
        "로컬 워커가 자동으로 시작합니다.\n"
        "워커가 없다면 터미널에서:\n"
        "<code>python worker.py --run-now</code>\n\n"
        f"대시보드: {dashboard_url}"
    )
    _tg_send(text)


# ── 파이프라인 상태 ───────────────────────────────────────────────

def _read_pipeline_status() -> dict:
    """data/pipeline_status.json 읽기"""
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"status": "idle", "updated_at": ""}


def _write_trigger(run_id: str, source: str = "dashboard") -> Path:
    """run_queue/ 에 트리거 파일 생성 → 로컬 워커가 감지"""
    data = {
        "run_id": run_id,
        "source": source,
        "requested_at": datetime.utcnow().isoformat(),
    }
    path = RUN_QUEUE_DIR / f"trigger_{run_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _trigger_n8n(run_id: str, trigger_data: dict) -> bool:
    """n8n 웹훅 트리거 (공개 URL인 경우만)"""
    if not N8N_WEBHOOK_BASE_URL:
        return False
    if "localhost" in N8N_WEBHOOK_BASE_URL or "127.0.0.1" in N8N_WEBHOOK_BASE_URL:
        return False
    try:
        data = json.dumps(trigger_data).encode("utf-8")
        req = urllib.request.Request(
            f"{N8N_WEBHOOK_BASE_URL}/pipeline-run",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        return True
    except Exception:
        return False


# ── 승인 관리 헬퍼 ────────────────────────────────────────────────

def _load_request(playlist_id: str) -> dict | None:
    for directory in [PENDING_DIR, APPROVED_DIR]:
        path = directory / f"{playlist_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_request(playlist_id: str, data: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{playlist_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _list_pending() -> list[dict]:
    result = []
    if PENDING_DIR.exists():
        for f in sorted(PENDING_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                result.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return result


# ── 라우트: 메인 ──────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    pending = _list_pending()
    return render_template("index.html", pending=pending)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == MASTER_PASSWORD:
            session["authenticated"] = True
            flash("로그인 성공", "success")
            return redirect(url_for("index"))
        flash("비밀번호가 틀렸습니다", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/review/<playlist_id>")
@login_required
def review(playlist_id: str):
    data = _load_request(playlist_id)
    if data is None:
        abort(404)
    return render_template("review.html", data=data, playlist_id=playlist_id)


# ── 라우트: 승인 API ──────────────────────────────────────────────

@app.route("/api/approve/<playlist_id>", methods=["POST"])
@login_required
def api_approve(playlist_id: str):
    data = _load_request(playlist_id)
    if data is None:
        return jsonify({"status": "error", "message": "not found"}), 404
    data["status"] = "approved"
    data["approved_at"] = datetime.utcnow().isoformat()
    data["feedback"] = request.json.get("feedback", "") if request.is_json else ""
    pending_path = PENDING_DIR / f"{playlist_id}.json"
    if pending_path.exists():
        pending_path.unlink()
    _save_request(playlist_id, data, APPROVED_DIR)

    # Telegram 알림
    _tg_send(
        f"✅ <b>[Velvet Radio] 마스터 승인</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 {playlist_id}\n"
        f"💬 {data.get('feedback') or '피드백 없음'}\n"
        f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
    )
    return jsonify({"status": "approved", "playlist_id": playlist_id})


@app.route("/api/reject/<playlist_id>", methods=["POST"])
@login_required
def api_reject(playlist_id: str):
    data = _load_request(playlist_id)
    if data is None:
        return jsonify({"status": "error"}), 404
    data["status"] = "rejected"
    data["rejected_at"] = datetime.utcnow().isoformat()
    data["rejection_reason"] = (
        request.json.get("reason", "마스터 거절") if request.is_json else "마스터 거절"
    )
    pending_path = PENDING_DIR / f"{playlist_id}.json"
    if pending_path.exists():
        pending_path.unlink()
    _save_request(playlist_id, data, APPROVED_DIR)
    return jsonify({"status": "rejected", "playlist_id": playlist_id})


@app.route("/api/revision/<playlist_id>", methods=["POST"])
@login_required
def api_revision(playlist_id: str):
    data = _load_request(playlist_id)
    if data is None:
        return jsonify({"status": "error"}), 404
    data["status"] = "revision_requested"
    data["revision_feedback"] = (
        request.json.get("feedback", "수정 필요") if request.is_json else "수정 필요"
    )
    _save_request(playlist_id, data, PENDING_DIR)
    return jsonify({"status": "revision_requested", "playlist_id": playlist_id})


@app.route("/api/status/<playlist_id>")
@login_required
def api_status(playlist_id: str):
    data = _load_request(playlist_id)
    status = data.get("status", "unknown") if data else "not_found"
    return jsonify({"playlist_id": playlist_id, "status": status})


@app.route("/api/pending")
@login_required
def api_pending():
    return jsonify(_list_pending())


# ── 라우트: 파이프라인 실행 ───────────────────────────────────────

@app.route("/api/run", methods=["POST"])
@login_required
def api_run():
    """
    파이프라인 수동 실행 트리거

    우선순위:
    1. data/run_queue/ 에 트리거 파일 생성 → 로컬 워커 감지
    2. n8n 웹훅 트리거 (공개 URL이 설정된 경우)
    3. Telegram 알림 발송 (항상 실행)

    워커는 python worker.py --watch 로 실행해두어야 합니다.
    """
    # 현재 실행 중인지 확인
    current_status = _read_pipeline_status()
    if current_status.get("status") == "running":
        return jsonify({
            "status": "already_running",
            "run_id": current_status.get("run_id", ""),
            "message": "파이프라인이 이미 실행 중입니다.",
            "started_at": current_status.get("started_at", ""),
        }), 409

    # Run ID 생성
    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:6]
    trigger_data = {
        "run_id": run_id,
        "source": "dashboard",
        "requested_at": datetime.utcnow().isoformat(),
    }

    triggered_via = []

    # 1. 트리거 파일 생성 (로컬 워커용)
    try:
        trigger_path = _write_trigger(run_id)
        triggered_via.append("trigger_file")
    except Exception as e:
        triggered_via.append(f"trigger_file_failed:{e}")

    # 2. n8n 웹훅 (공개 URL인 경우)
    if _trigger_n8n(run_id, trigger_data):
        triggered_via.append("n8n")

    # 3. Telegram 알림 (항상 시도)
    _tg_send_run_request(run_id)
    triggered_via.append("telegram_notified")

    return jsonify({
        "status": "triggered",
        "run_id": run_id,
        "triggered_via": triggered_via,
        "message": (
            "파이프라인 실행 요청됨. "
            "로컬 워커(python worker.py --watch)가 실행 중이면 자동으로 시작됩니다."
        ),
        "worker_command": "python worker.py --run-now",
    })


@app.route("/api/pipeline/status")
@login_required
def api_pipeline_status():
    """현재 파이프라인 실행 상태 반환"""
    return jsonify(_read_pipeline_status())


# ── 라우트: Telegram 웹훅 ─────────────────────────────────────────

@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}

    # ── 인라인 버튼 콜백 처리 ──────────────────────────────────
    callback = data.get("callback_query", {})
    callback_data = callback.get("data", "")

    if callback_data:
        parts = callback_data.split(":", 1)
        if len(parts) != 2:
            return jsonify({"ok": False}), 400

        action, target_id = parts

        # 승인/거절
        if action in ("approve", "reject"):
            req_data = _load_request(target_id)
            if req_data is None:
                return jsonify({"ok": False, "message": "not found"}), 404

            if action == "approve":
                req_data["status"] = "approved"
                req_data["approved_at"] = datetime.utcnow().isoformat()
            else:
                req_data["status"] = "rejected"
                req_data["rejected_at"] = datetime.utcnow().isoformat()

            pending_path = PENDING_DIR / f"{target_id}.json"
            if pending_path.exists():
                pending_path.unlink()
            _save_request(target_id, req_data, APPROVED_DIR)

        # 수정 요청
        elif action == "revision":
            req_data = _load_request(target_id)
            if req_data:
                req_data["status"] = "revision_requested"
                _save_request(target_id, req_data, PENDING_DIR)

        return jsonify({"ok": True})

    # ── 일반 메시지 처리 (/run 커맨드) ─────────────────────────
    msg = data.get("message", {})
    text = (msg.get("text") or "").strip().lower()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    admin_chat_id = TELEGRAM_CHAT_ID

    if text in ["/run", "/run@velvetradiobot"] and chat_id == admin_chat_id:
        # 실행 중 확인
        current = _read_pipeline_status()
        if current.get("status") == "running":
            _tg_send(
                f"⚠️ 파이프라인이 이미 실행 중입니다.\n"
                f"Run ID: <code>{current.get('run_id', '')}</code>"
            )
            return jsonify({"ok": True})

        # 트리거 생성
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_tg"
        _write_trigger(run_id, source="telegram_command")
        _tg_send(
            f"✅ 실행 요청 접수됨\n"
            f"Run ID: <code>{run_id}</code>\n"
            f"로컬 워커가 실행 중이면 곧 시작됩니다."
        )

    elif text == "/status":
        status = _read_pipeline_status()
        icons = {"idle": "😴", "running": "⏳", "completed": "✅", "failed": "❌"}
        s = status.get("status", "idle")
        reply = (
            f"{icons.get(s, '?')} <b>파이프라인 상태: {s.upper()}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
        )
        if status.get("run_id"):
            reply += f"🆔 Run ID: <code>{status['run_id']}</code>\n"
        if status.get("updated_at"):
            reply += f"⏰ {status['updated_at'][:19].replace('T', ' ')} UTC\n"
        if status.get("error"):
            reply += f"❌ 오류: {status['error'][:200]}\n"
        _tg_send(reply)

    return jsonify({"ok": True})


# ── 라우트: Suno 웹훅 + 결과 조회 ───────────────────────────────────

@app.route("/webhook/suno", methods=["POST"])
def suno_webhook():
    """
    SunoAPI.org 생성 완료 콜백 수신
    결과를 data/suno_callbacks/{taskId}.json 에 저장
    """
    data = request.get_json(silent=True) or {}

    # SunoAPI.org 콜백 형식: {"taskId": "...", "status": "complete", "data": [...]}
    task_id = data.get("taskId") or data.get("task_id") or data.get("id")
    if not task_id:
        return jsonify({"ok": False, "message": "taskId missing"}), 400

    status = data.get("status", "unknown")
    clips = data.get("data") or data.get("clips") or []

    result = {
        "task_id": task_id,
        "status": status,
        "received_at": datetime.utcnow().isoformat(),
        "raw": data,
    }

    # 오디오 URL 추출 (배열의 첫 번째 아이템)
    if clips and isinstance(clips, list):
        clip = clips[0] if isinstance(clips[0], dict) else {}
        result["audio_url"] = clip.get("audio_url") or clip.get("url") or ""
        result["title"] = clip.get("title") or clip.get("song_name") or ""
        result["duration"] = clip.get("duration") or 0
    elif isinstance(data.get("audio_url"), str):
        result["audio_url"] = data["audio_url"]

    try:
        SUNO_CALLBACKS_DIR.mkdir(parents=True, exist_ok=True)
        (SUNO_CALLBACKS_DIR / f"{task_id}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

    return jsonify({"ok": True, "task_id": task_id, "status": status})


@app.route("/api/suno/result/<task_id>")
def api_suno_result(task_id: str):
    """
    Suno 생성 결과 조회 (로컬 워커 폴링용)
    인증 불필요 — taskId 를 알아야만 접근 가능
    """
    result_file = SUNO_CALLBACKS_DIR / f"{task_id}.json"
    if not result_file.exists():
        return jsonify({"status": "pending", "task_id": task_id}), 404
    try:
        return jsonify(json.loads(result_file.read_text(encoding="utf-8")))
    except Exception:
        return jsonify({"status": "error", "task_id": task_id}), 500


# ── 세션 헬퍼 ────────────────────────────────────────────────────

def _list_sessions() -> list[dict]:
    result = []
    for f in sorted(SESSION_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            result.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return result


def _load_session_file(session_id: str) -> dict | None:
    path = SESSION_DIR / f"{session_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _write_phase_trigger(run_id: str, phase: int, session_id: str, source: str = "studio") -> Path:
    """Phase 단독 실행 트리거 파일 생성"""
    data = {
        "run_id": run_id,
        "phase": phase,
        "session_id": session_id,
        "source": source,
        "requested_at": datetime.utcnow().isoformat(),
    }
    path = RUN_QUEUE_DIR / f"trigger_{run_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ── 라우트: Pipeline Studio ───────────────────────────────────────

@app.route("/pipeline-studio")
@login_required
def pipeline_studio():
    return render_template("pipeline_studio.html")


@app.route("/api/sessions")
@login_required
def api_sessions():
    if IS_VERCEL:
        return _vercel_local_only()
    sessions = _list_sessions()
    # 각 세션에서 핵심 정보만 반환
    summary = []
    for s in sessions:
        phases = s.get("phases", {})
        summary.append({
            "session_id": s.get("session_id", ""),
            "created_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "current_phase": s.get("current_phase", 1),
            "playlist_id": s.get("playlist_id", ""),
            "phases": {
                k: {"status": v.get("status"), "duration_seconds": v.get("duration_seconds")}
                for k, v in phases.items()
            },
            "phase1": {"theme": phases.get("phase1", {}).get("theme", "")},
        })
    return jsonify(summary)


@app.route("/api/session/<session_id>")
@login_required
def api_session_detail(session_id: str):
    if IS_VERCEL:
        return _vercel_local_only()
    data = _load_session_file(session_id)
    if data is None:
        return jsonify({"error": "not found"}), 404
    # quality_report_json / video_asset_json은 크기가 크므로 제외
    for phase_key in data.get("phases", {}).values():
        phase_key.pop("quality_report_json", None)
        phase_key.pop("video_asset_json", None)
    return jsonify(data)


@app.route("/api/phase/run", methods=["POST"])
@login_required
def api_phase_run():
    """특정 Phase 단독 실행 트리거"""
    if IS_VERCEL:
        return _vercel_local_only()
    body = request.get_json(silent=True) or {}
    phase = body.get("phase")
    session_id = body.get("session_id") or ""

    if phase not in (1, 2, 3, 4):
        return jsonify({"status": "error", "message": "phase는 1~4 사이의 정수입니다."}), 400

    # Phase 1이면 새 session_id 자동 생성
    if phase == 1:
        session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    elif not session_id:
        return jsonify({"status": "error", "message": "phase 2~4는 session_id가 필요합니다."}), 400

    # 실행 중 체크
    current = _read_pipeline_status()
    if current.get("status") == "running":
        return jsonify({
            "status": "already_running",
            "run_id": current.get("run_id", ""),
            "message": "파이프라인이 이미 실행 중입니다.",
        }), 409

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + f"_p{phase}"
    _write_phase_trigger(run_id, phase, session_id, source="studio")

    _tg_send(
        f"🎛️ <b>[Pipeline Studio] Phase {phase} 실행 요청</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 Session: <code>{session_id}</code>\n"
        f"🔢 Phase: {phase}\n"
        f"<code>python worker.py --phase {phase} --session {session_id}</code>"
    )

    return jsonify({
        "status": "triggered",
        "run_id": run_id,
        "session_id": session_id,
        "phase": phase,
        "worker_command": f"python worker.py --phase {phase} --session {session_id}",
        "message": f"Phase {phase} 실행 요청됨. 워커가 실행 중이어야 합니다.",
    })


@app.route("/api/session/<session_id>/log")
@login_required
def api_session_log(session_id: str):
    """세션 활동 로그 파일 반환 (data/sessions/<id>.log)"""
    if IS_VERCEL:
        return _vercel_local_only()
    log_path = SESSION_DIR / f"{session_id}.log"
    if not log_path.exists():
        return jsonify({"lines": [], "total": 0})
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = int(request.args.get("offset", 0))
        return jsonify({"lines": lines[offset:], "total": len(lines)})
    except Exception as e:
        return jsonify({"lines": [], "total": 0, "error": str(e)})


@app.route("/api/playlist/<playlist_id>")
@login_required
def api_playlist_detail(playlist_id: str):
    if IS_VERCEL:
        return _vercel_local_only()
    path = PLAYLISTS_DIR / f"{playlist_id}.json"
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    try:
        return jsonify(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 라우트: 미디어 / 헬스 ─────────────────────────────────────────

@app.route("/media/<path:filename>")
@login_required
def serve_media(filename: str):
    return send_from_directory(str(DATA_DIR), filename)


@app.route("/health")
def health():
    pipeline_status = _read_pipeline_status()
    return jsonify({
        "status": "ok",
        "service": "velvet-radio-dashboard",
        "data_dir": str(DATA_DIR),
        "pending_count": len(list(PENDING_DIR.glob("*.json"))) if PENDING_DIR.exists() else 0,
        "pipeline": pipeline_status.get("status", "idle"),
        "pipeline_run_id": pipeline_status.get("run_id", ""),
    })


# Vercel WSGI 진입점
application = app

if __name__ == "__main__":
    app.run(
        host=os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.environ.get("DASHBOARD_PORT", 8080)),
        debug=False,
    )
