"""Ghost Font CAPTCHA - production-ready, embeddable service (no API keys).

Routes
------
    GET  /                       Demo page showing the drop-in integration.
    GET  /api.js                 The drop-in widget script.
    GET  /widget                 The iframe widget page.
    GET  /api/captcha/challenge  Issue a challenge_id + video URL.
    GET  /api/captcha/video?id=  Stream the pre-rendered MP4 for a challenge.
    POST /api/captcha/verify     Widget->server: check the code, mint a token.
    POST /api/siteverify         Server->server: redeem a token.
    POST /demo/submit            Mock publisher endpoint used by the demo page.

No API keys are required.  The widget is identified by the embedding origin,
which is validated against the optional ``ALLOWED_ORIGINS`` list.

See README.md for the full integration guide.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_from_directory,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import captcha
import config
from config import (
    ALLOWED_ORIGINS,
    CHALLENGE_TTL,
    PUBLIC_BASE_URL,
    RATE_CHALLENGE,
    RATE_SITEVERIFY,
    RATE_VERIFY,
    SECRET_KEY,
    TOKEN_TTL,
    origin_allowed,
)
from pool import pool
from store import Challenge, Token, store

# --------------------------------------------------------------------------- #
# App setup
# --------------------------------------------------------------------------- #
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri=config.REDIS_URL or "memory://",
    headers_enabled=True,
)

# In-process index mapping challenge_id -> mp4 bytes.  Cleared when the video
# is streamed.  This keeps the pre-rendered video paired with its challenge
# without re-rendering on demand.
_video_lock = threading.Lock()
_video_index: dict[str, bytes] = {}


# Start the pre-generation pool as soon as the module is imported so the
# first real request is served from the pool rather than rendering on demand.
pool.start()


# --------------------------------------------------------------------------- #
# Security headers
# --------------------------------------------------------------------------- #
@app.after_request
def _security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Intentionally no X-Frame-Options: the widget is meant to be embedded in
    # third-party iframes.  CSP frame-ancestors controls framing instead.
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), microphone=(), geolocation=()"
    )
    # The widget is embedded in iframes on third-party sites, so allow framing.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; "
        "media-src 'self' blob:; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors *;"
    )
    return response


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _new_id() -> str:
    return secrets.token_urlsafe(18)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_error(message: str, status: int, error_codes: list[str] | None = None):
    payload: dict = {"success": False, "message": message}
    if error_codes:
        payload["error-codes"] = error_codes
    return jsonify(payload), status


# --------------------------------------------------------------------------- #
# Demo page + drop-in script
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    """Render the integration demo page."""
    return render_template(
        "demo.html",
        public_base_url=PUBLIC_BASE_URL.rstrip("/"),
        api_js_url=f"{PUBLIC_BASE_URL.rstrip('/')}/api.js",
    )


@app.route("/api.js")
def api_js():
    """Serve the drop-in widget script with a long cache lifetime."""
    response = send_from_directory(app.static_folder, "ghost-captcha.js")
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return response


# --------------------------------------------------------------------------- #
# Widget iframe page
# --------------------------------------------------------------------------- #
@app.route("/widget")
def widget():
    """Render the iframe widget page for a given origin."""
    origin = request.args.get("origin", "")
    theme = request.args.get("theme", "dark")
    lang = request.args.get("lang", "en")

    return render_template(
        "widget.html",
        origin=origin,
        theme=theme if theme in ("dark", "light") else "dark",
        lang=lang,
    )


# --------------------------------------------------------------------------- #
# Widget-facing API (iframe -> this server)
# --------------------------------------------------------------------------- #
@app.after_request
def _cors_for_widget_api(response: Response) -> Response:
    """Allow cross-origin requests from the sandboxed widget iframe."""
    if request.path.startswith(("/api/captcha/challenge", "/api/captcha/verify")):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Expose-Headers"] = "Content-Type"
    return response


@app.route("/api/captcha/challenge", methods=["OPTIONS"])
@app.route("/api/captcha/verify", methods=["OPTIONS"])
def _widget_preflight():
    """Respond to CORS preflight requests from the widget."""
    return "", 204


@app.route("/api/captcha/challenge", methods=["GET"])
@limiter.limit(RATE_CHALLENGE)
def get_challenge():
    """Issue a challenge_id and a video URL for the requesting origin."""
    origin = request.args.get("origin", "")
    if not origin_allowed(origin):
        return _json_error("Origin not allowed.", 403, ["origin-not-allowed"])

    # Ensure the pre-generation pool worker is alive (restarts it after fork).
    pool.ensure_running()

    pooled = pool.get(timeout=5.0)
    if pooled is None:
        # Pool empty: render one on demand rather than failing the user.
        code = captcha.generate_code()
        try:
            video = captcha.render_video_bytes(code)
        except Exception:
            return _json_error("Failed to generate CAPTCHA.", 503, ["backend-error"])
    else:
        code, video = pooled.code, pooled.video

    challenge_id = _new_id()
    store.put_challenge(
        challenge_id,
        Challenge(code=code, origin=origin, created_at=time.time()),
    )
    # Pair the video bytes with the challenge so the video endpoint can stream
    # them without re-rendering.
    with _video_lock:
        _video_index[challenge_id] = video

    response = jsonify(
        {
            "challenge_id": challenge_id,
            "video_url": f"{PUBLIC_BASE_URL.rstrip('/')}/api/captcha/video?id={challenge_id}",
            "expires_in": CHALLENGE_TTL,
        }
    )
    return response


@app.route("/api/captcha/video", methods=["GET"])
def get_challenge_video():
    """Stream the pre-rendered MP4 for a challenge_id without consuming it."""
    challenge_id = request.args.get("id", "")
    if not challenge_id:
        return _json_error("Missing challenge id.", 400, ["missing-input"])

    # Confirm the challenge still exists (peek, don't consume).
    challenge = store.peek_challenge(challenge_id)
    if challenge is None:
        # Clean up any stale video index entry.
        with _video_lock:
            _video_index.pop(challenge_id, None)
        return _json_error("Challenge expired or not found.", 404, ["challenge-not-found"])

    # Use .get() instead of .pop() so the browser can re-request the video
    # if it needs to reload/seek without getting a 404.
    with _video_lock:
        video = _video_index.get(challenge_id)
    if video is None:
        return _json_error("Video not available.", 404, ["video-not-found"])

    return Response(
        video,
        mimetype="video/mp4",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
        },
    )


# --------------------------------------------------------------------------- #
# Widget verification (iframe -> server)
# --------------------------------------------------------------------------- #
@app.route("/api/captcha/verify", methods=["POST"])
@limiter.limit(RATE_VERIFY)
def verify_captcha():
    """Verify the user's typed code against the stored challenge; mint a token."""
    data = request.get_json(silent=True) or {}
    origin = data.get("origin", "")
    challenge_id = data.get("challenge_id", "")
    user_input = (data.get("code") or "").strip().upper()

    if not origin_allowed(origin):
        return _json_error("Origin not allowed.", 403, ["origin-not-allowed"])
    if not challenge_id:
        return _json_error("Missing challenge id.", 400, ["missing-input"])
    if not user_input:
        return _json_error("Please enter the code.", 400, ["missing-input"])

    challenge = store.pop_challenge(challenge_id)
    if challenge is None:
        return _json_error("CAPTCHA expired. Please refresh.", 400, ["challenge-expired"])

    if user_input != challenge.code.upper():
        return _json_error("Incorrect code. Try again.", 401, ["incorrect-code"])

    token = _new_id()
    store.put_token(
        token,
        Token(origin=origin, created_at=time.time()),
    )
    # Clean up any lingering video bytes.
    with _video_lock:
        _video_index.pop(challenge_id, None)

    return jsonify(
        {
            "success": True,
            "message": "Verified successfully!",
            "token": token,
            "expires_in": TOKEN_TTL,
        }
    )


# --------------------------------------------------------------------------- #
# Token verification helper (used by siteverify and the demo endpoint)
# --------------------------------------------------------------------------- #
def _verify_token(token: str) -> tuple[bool, dict, int]:
    """Validate a response token.  Mirrors the Turnstile API shape."""
    if not token:
        return False, {"success": False, "error-codes": ["missing-input-response"]}, 400

    record = store.pop_token(token)
    if record is None:
        return False, {"success": False, "error-codes": ["invalid-or-expired-token"]}, 400

    return (
        True,
        {
            "success": True,
            "challenge_ts": _iso_now(),
            "hostname": record.origin,
            "metadata": {},
        },
        200,
    )


# --------------------------------------------------------------------------- #
# Publisher verification (server-to-server, Turnstile-style)
# --------------------------------------------------------------------------- #
@app.route("/api/siteverify", methods=["POST"])
@limiter.limit(RATE_SITEVERIFY)
def siteverify():
    """Redeem a token.  No secret key is required."""
    data = request.get_json(silent=True) or {}
    if not data:
        data = request.form.to_dict()

    token = data.get("response", "")
    ok, result, status = _verify_token(token)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# Demo publisher endpoint
# --------------------------------------------------------------------------- #
@app.route("/demo/submit", methods=["POST"])
def demo_submit():
    """Mock publisher endpoint: verifies the token via the same helper."""
    data = request.get_json(silent=True) or {}
    token = (
        data.get("ghost-captcha-response")
        or data.get("g-ghost-captcha-response")
        or ""
    )
    if not token:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "No token submitted.",
                    "error-codes": ["missing-input-response"],
                }
            ),
            400,
        )

    ok, result, status = _verify_token(token)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    pool.start()
    try:
        app.run(debug=True, port=5000)
    finally:
        pool.stop()
