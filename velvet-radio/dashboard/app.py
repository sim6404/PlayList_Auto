"""
Velvet Radio — 마스터 승인 대시보드
Flask 기반 웹 UI: 영상 검토 → 승인/거절/수정 요청

실행: python -m dashboard.app
접속: http://localhost:8080
Vercel: vercel.json 통해 서버리스 배포
"""
from __future__ import annotations

import json
import os
import sys
from functools import wraps
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_from_directory,
)

# Vercel 서버리스 환경: /tmp 를 데이터 디렉토리로 사용
if os.environ.get("VERCEL"):
    os.environ.setdefault("DATA_DIR", "/tmp/data")
    os.environ.setdefault("LOG_DIR", "/tmp/logs")
    Path("/tmp/data/pending_approval").mkdir(parents=True, exist_ok=True)
    Path("/tmp/data/approved").mkdir(parents=True, exist_ok=True)

from src.common.config_loader import config
from src.common.logger import get_logger
from src.phase4_publish.approval_manager import ApprovalManager

logger = get_logger(__name__)

IS_VERCEL = bool(os.environ.get("VERCEL"))

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = config.dashboard_secret_key

approval_manager = ApprovalManager()


@app.context_processor
def inject_globals():
    return {"is_vercel": IS_VERCEL}


# ── 인증 미들웨어 ──────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── 라우트 ────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    """대시보드 홈 — 대기 중인 승인 목록"""
    pending = approval_manager.list_pending()
    return render_template("index.html", pending=pending)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == config.master_password:
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
    """플레이리스트 상세 검토 페이지"""
    pending_path = config.data_dir / "pending_approval" / f"{playlist_id}.json"
    approved_path = config.data_dir / "approved" / f"{playlist_id}.json"

    data_path = pending_path if pending_path.exists() else approved_path
    if not data_path.exists():
        abort(404)

    data = json.loads(data_path.read_text(encoding="utf-8"))
    return render_template("review.html", data=data, playlist_id=playlist_id)


@app.route("/api/approve/<playlist_id>", methods=["POST"])
@login_required
def api_approve(playlist_id: str):
    """승인 API"""
    feedback = request.json.get("feedback", "") if request.is_json else ""
    success = approval_manager.approve(playlist_id, feedback)
    if success:
        logger.info("대시보드 승인", playlist_id=playlist_id)
        return jsonify({"status": "approved", "playlist_id": playlist_id})
    return jsonify({"status": "error", "message": "승인 처리 실패"}), 400


@app.route("/api/reject/<playlist_id>", methods=["POST"])
@login_required
def api_reject(playlist_id: str):
    """거절 API"""
    reason = request.json.get("reason", "마스터 거절") if request.is_json else "마스터 거절"
    success = approval_manager.reject(playlist_id, reason)
    if success:
        return jsonify({"status": "rejected", "playlist_id": playlist_id})
    return jsonify({"status": "error"}), 400


@app.route("/api/revision/<playlist_id>", methods=["POST"])
@login_required
def api_revision(playlist_id: str):
    """수정 요청 API"""
    feedback = request.json.get("feedback", "수정 필요") if request.is_json else "수정 필요"
    success = approval_manager.request_revision(playlist_id, feedback)
    if success:
        return jsonify({"status": "revision_requested", "playlist_id": playlist_id})
    return jsonify({"status": "error"}), 400


@app.route("/api/status/<playlist_id>")
@login_required
def api_status(playlist_id: str):
    """현재 승인 상태 폴링용"""
    status = approval_manager.get_status(playlist_id)
    return jsonify({"playlist_id": playlist_id, "status": status.value})


@app.route("/api/pending")
@login_required
def api_pending():
    """대기 중인 요청 목록"""
    return jsonify(approval_manager.list_pending())


@app.route("/api/run", methods=["POST"])
@login_required
def api_run():
    """파이프라인 실행 트리거 — run_queue에 트리거 파일 생성"""
    from datetime import datetime

    status_file = config.data_dir / "pipeline_status.json"
    if status_file.exists():
        try:
            st = json.loads(status_file.read_text(encoding="utf-8"))
            if st.get("status") == "running":
                return jsonify({"status": "already_running", "run_id": st.get("run_id", "")}), 409
        except Exception:
            pass

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(3).hex()
    trigger = {
        "run_id": run_id,
        "source": "dashboard",
        "requested_at": datetime.utcnow().isoformat(),
    }
    run_queue_dir = config.data_dir / "run_queue"
    run_queue_dir.mkdir(parents=True, exist_ok=True)
    trigger_path = run_queue_dir / f"trigger_{run_id}.json"
    trigger_path.write_text(json.dumps(trigger, ensure_ascii=False), encoding="utf-8")

    status_file.write_text(
        json.dumps({"status": "triggered", "run_id": run_id,
                    "updated_at": datetime.utcnow().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("파이프라인 트리거 생성", run_id=run_id)
    return jsonify({
        "status": "triggered",
        "run_id": run_id,
        "message": "트리거 파일 생성됨. 로컬 워커(python worker.py --watch)가 자동으로 실행합니다.",
    })


# ── Pipeline Studio ───────────────────────────────────────────────

def _vercel_local_only():
    """Vercel 환경에서 로컬 전용 API 호출 시 반환할 에러"""
    return jsonify({
        "error": "local_only",
        "message": "Pipeline Studio는 로컬 환경에서만 사용할 수 있습니다.",
        "hint": "로컬에서 실행: python -m dashboard.app  →  http://localhost:8080/pipeline-studio",
    }), 503


@app.route("/pipeline-studio")
@login_required
def pipeline_studio():
    """Pipeline Studio 페이지"""
    return render_template("pipeline_studio.html")


@app.route("/api/pipeline/status")
@login_required
def api_pipeline_status():
    """파이프라인 실행 상태 반환"""
    if IS_VERCEL:
        return jsonify({"status": "idle", "updated_at": "",
                        "vercel_note": "로컬 워커 상태는 Vercel에서 조회할 수 없습니다."})
    status_file = config.data_dir / "pipeline_status.json"
    if status_file.exists():
        try:
            return jsonify(json.loads(status_file.read_text(encoding="utf-8")))
        except Exception:
            pass
    return jsonify({"status": "idle", "updated_at": ""})


@app.route("/api/sessions")
@login_required
def api_sessions():
    """세션 목록 반환"""
    if IS_VERCEL:
        return _vercel_local_only()
    session_dir = config.data_dir / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(session_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            sessions.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return jsonify(sessions)


@app.route("/api/session/<session_id>")
@login_required
def api_session(session_id: str):
    """세션 상세 정보"""
    if IS_VERCEL:
        return _vercel_local_only()
    path = config.data_dir / "sessions" / f"{session_id}.json"
    if not path.exists():
        return jsonify({"error": "세션을 찾을 수 없습니다"}), 404
    try:
        return jsonify(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/session/<session_id>/log")
@login_required
def api_session_log(session_id: str):
    """세션 로그 (offset 기반 스트리밍)"""
    if IS_VERCEL:
        return _vercel_local_only()
    offset = int(request.args.get("offset", 0))
    log_path = config.data_dir / "sessions" / f"{session_id}.log"
    if not log_path.exists():
        return jsonify({"lines": [], "offset": 0})
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        new_lines = lines[offset:]
        return jsonify({"lines": new_lines, "offset": len(lines)})
    except Exception as e:
        return jsonify({"lines": [], "offset": offset, "error": str(e)})


@app.route("/api/playlist/<playlist_id>")
@login_required
def api_playlist(playlist_id: str):
    """플레이리스트 데이터"""
    if IS_VERCEL:
        return _vercel_local_only()
    path = config.data_dir / "playlists" / f"{playlist_id}.json"
    if not path.exists():
        return jsonify({"error": "플레이리스트를 찾을 수 없습니다"}), 404
    try:
        return jsonify(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/phase/run", methods=["POST"])
@login_required
def api_phase_run():
    """특정 Phase 단독 실행 트리거 (Pipeline Studio용)"""
    if IS_VERCEL:
        return _vercel_local_only()
    from datetime import datetime

    body = request.get_json(silent=True) or {}
    phase_num = body.get("phase")
    session_id = body.get("session_id", "").strip()

    if phase_num not in (1, 2, 3, 4):
        return jsonify({"error": "phase는 1~4 사이 값이어야 합니다"}), 400
    if phase_num > 1 and not session_id:
        return jsonify({"error": "Phase 2 이상은 session_id가 필요합니다"}), 400

    if not session_id:
        session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + f"_p{phase_num}"
    trigger = {
        "run_id": run_id,
        "phase": phase_num,
        "session_id": session_id,
        "source": "studio",
        "requested_at": datetime.utcnow().isoformat(),
    }
    run_queue_dir = config.data_dir / "run_queue"
    run_queue_dir.mkdir(parents=True, exist_ok=True)
    trigger_path = run_queue_dir / f"trigger_{run_id}.json"
    trigger_path.write_text(json.dumps(trigger, ensure_ascii=False), encoding="utf-8")

    status_file = config.data_dir / "pipeline_status.json"
    status_file.write_text(
        json.dumps({"status": "triggered", "run_id": run_id, "phase": phase_num,
                    "session_id": session_id, "updated_at": datetime.utcnow().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Phase 트리거 생성", phase=phase_num, session_id=session_id, run_id=run_id)
    return jsonify({
        "status": "triggered",
        "run_id": run_id,
        "session_id": session_id,
        "worker_command": f"python worker.py --phase {phase_num} --session {session_id}",
    })


# Telegram Webhook
@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}
    callback = data.get("callback_query", {})
    callback_data = callback.get("data", "")
    if callback_data:
        success = approval_manager.handle_telegram_callback(callback_data)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400


@app.route("/media/<path:filename>")
@login_required
def serve_media(filename: str):
    return send_from_directory(str(config.data_dir), filename)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "velvet-radio-dashboard"})


# Vercel WSGI 진입점
app_wsgi = app

if __name__ == "__main__":
    app.run(
        host=os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.environ.get("DASHBOARD_PORT", 8080)),
        debug=False,
    )
