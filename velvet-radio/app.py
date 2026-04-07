"""
Velvet Radio — Vercel 서버리스 진입점
이 파일은 velvet-radio/ 루트에 위치하여 src/, config/ 를 직접 참조합니다.
로컬 개발: velvet-radio/dashboard/app.py 를 사용하세요.
"""
from __future__ import annotations

import json
import os
import sys
from functools import wraps
from pathlib import Path

# 프로젝트 루트 (이 파일 위치 = velvet-radio/)
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

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

# Vercel 서버리스: /tmp 사용 (vercel.json env에서도 설정됨)
if os.environ.get("VERCEL"):
    os.environ.setdefault("DATA_DIR", "/tmp/data")
    os.environ.setdefault("LOG_DIR", "/tmp/logs")
    Path("/tmp/data/pending_approval").mkdir(parents=True, exist_ok=True)
    Path("/tmp/data/approved").mkdir(parents=True, exist_ok=True)

from src.common.config_loader import config
from src.common.logger import get_logger
from src.phase4_publish.approval_manager import ApprovalManager

logger = get_logger(__name__)

app = Flask(
    __name__,
    template_folder="dashboard/templates",
    static_folder="dashboard/static",
)
app.secret_key = config.dashboard_secret_key

approval_manager = ApprovalManager()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
@login_required
def index():
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
    feedback = request.json.get("feedback", "") if request.is_json else ""
    success = approval_manager.approve(playlist_id, feedback)
    if success:
        return jsonify({"status": "approved", "playlist_id": playlist_id})
    return jsonify({"status": "error", "message": "승인 처리 실패"}), 400


@app.route("/api/reject/<playlist_id>", methods=["POST"])
@login_required
def api_reject(playlist_id: str):
    reason = request.json.get("reason", "마스터 거절") if request.is_json else "마스터 거절"
    success = approval_manager.reject(playlist_id, reason)
    if success:
        return jsonify({"status": "rejected", "playlist_id": playlist_id})
    return jsonify({"status": "error"}), 400


@app.route("/api/revision/<playlist_id>", methods=["POST"])
@login_required
def api_revision(playlist_id: str):
    feedback = request.json.get("feedback", "수정 필요") if request.is_json else "수정 필요"
    success = approval_manager.request_revision(playlist_id, feedback)
    if success:
        return jsonify({"status": "revision_requested", "playlist_id": playlist_id})
    return jsonify({"status": "error"}), 400


@app.route("/api/status/<playlist_id>")
@login_required
def api_status(playlist_id: str):
    status = approval_manager.get_status(playlist_id)
    return jsonify({"playlist_id": playlist_id, "status": status.value})


@app.route("/api/pending")
@login_required
def api_pending():
    return jsonify(approval_manager.list_pending())


@app.route("/api/run", methods=["POST"])
@login_required
def api_run():
    import asyncio
    import threading

    def run_bg():
        asyncio.run(__import__(
            "src.phase4_publish.scheduler", fromlist=["run_full_pipeline"]
        ).run_full_pipeline())

    threading.Thread(target=run_bg, daemon=True).start()
    flash("파이프라인이 백그라운드에서 시작되었습니다", "success")
    return redirect(url_for("index"))


@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}
    callback = data.get("callback_query", {})
    callback_data = callback.get("data", "")
    if callback_data:
        approval_manager.handle_telegram_callback(callback_data)
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
application = app

if __name__ == "__main__":
    app.run(
        host=os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.environ.get("DASHBOARD_PORT", 8080)),
        debug=False,
    )
