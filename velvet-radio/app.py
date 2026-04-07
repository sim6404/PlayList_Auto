"""
Velvet Radio — Vercel 서버리스 진입점 (독립형)
src/ 임포트 없이 Flask + 환경변수만 사용
"""
from __future__ import annotations

import json
import os
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

PENDING_DIR.mkdir(parents=True, exist_ok=True)
APPROVED_DIR.mkdir(parents=True, exist_ok=True)

# ── Flask 앱 ──────────────────────────────────────────────────────
app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get("DASHBOARD_SECRET_KEY", "velvet-radio-dev-secret")

MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "velvet2024")


# ── 인증 ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


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


# ── 라우트 ────────────────────────────────────────────────────────

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


@app.route("/api/approve/<playlist_id>", methods=["POST"])
@login_required
def api_approve(playlist_id: str):
    data = _load_request(playlist_id)
    if data is None:
        return jsonify({"status": "error", "message": "not found"}), 404
    data["status"] = "approved"
    data["approved_at"] = datetime.utcnow().isoformat()
    data["feedback"] = request.json.get("feedback", "") if request.is_json else ""
    # 대기 파일 삭제 후 승인 디렉토리로 이동
    pending_path = PENDING_DIR / f"{playlist_id}.json"
    if pending_path.exists():
        pending_path.unlink()
    _save_request(playlist_id, data, APPROVED_DIR)
    return jsonify({"status": "approved", "playlist_id": playlist_id})


@app.route("/api/reject/<playlist_id>", methods=["POST"])
@login_required
def api_reject(playlist_id: str):
    data = _load_request(playlist_id)
    if data is None:
        return jsonify({"status": "error"}), 404
    data["status"] = "rejected"
    data["rejected_at"] = datetime.utcnow().isoformat()
    data["rejection_reason"] = request.json.get("reason", "마스터 거절") if request.is_json else "마스터 거절"
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
    data["revision_feedback"] = request.json.get("feedback", "수정 필요") if request.is_json else "수정 필요"
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


@app.route("/api/run", methods=["POST"])
@login_required
def api_run():
    # Vercel 서버리스 환경에서는 파이프라인 직접 실행 불가
    # 로컬 Worker 또는 n8n 웹훅으로 트리거 필요
    flash("파이프라인은 로컬 Worker 또는 n8n에서 실행해주세요", "info")
    return redirect(url_for("index"))


@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}
    callback = data.get("callback_query", {})
    callback_data = callback.get("data", "")

    if not callback_data:
        return jsonify({"ok": False}), 400

    # 형식: "approve:playlist_id" | "reject:playlist_id"
    parts = callback_data.split(":", 1)
    if len(parts) != 2:
        return jsonify({"ok": False}), 400

    action, playlist_id = parts
    req_data = _load_request(playlist_id)
    if req_data is None:
        return jsonify({"ok": False, "message": "not found"}), 404

    if action == "approve":
        req_data["status"] = "approved"
        req_data["approved_at"] = datetime.utcnow().isoformat()
        pending_path = PENDING_DIR / f"{playlist_id}.json"
        if pending_path.exists():
            pending_path.unlink()
        _save_request(playlist_id, req_data, APPROVED_DIR)
    elif action == "reject":
        req_data["status"] = "rejected"
        req_data["rejected_at"] = datetime.utcnow().isoformat()
        pending_path = PENDING_DIR / f"{playlist_id}.json"
        if pending_path.exists():
            pending_path.unlink()
        _save_request(playlist_id, req_data, APPROVED_DIR)

    return jsonify({"ok": True})


@app.route("/media/<path:filename>")
@login_required
def serve_media(filename: str):
    return send_from_directory(str(DATA_DIR), filename)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "velvet-radio-dashboard",
        "data_dir": str(DATA_DIR),
        "pending_count": len(list(PENDING_DIR.glob("*.json"))) if PENDING_DIR.exists() else 0,
    })


# Vercel WSGI 진입점
application = app

if __name__ == "__main__":
    app.run(
        host=os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.environ.get("DASHBOARD_PORT", 8080)),
        debug=False,
    )
