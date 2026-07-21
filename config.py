"""Configuration for the Ghost Font CAPTCHA service.

All settings are read from environment variables with sensible defaults so the
service runs out-of-the-box for local development and can be hardened for
production by setting a few env vars.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

# --------------------------------------------------------------------------- #
# Core environment configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent

# Public URL where the service is hosted (used to build widget/script URLs).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")

# Flask secret.  Used for session signing if ever needed; not user-facing.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Redis connection string.  When set, the token store and rate-limiter use
# Redis (horizontally scalable).  When unset, an in-process fallback is used.
REDIS_URL = os.environ.get("REDIS_URL")

# Comma-separated list of origins that are allowed to embed the widget.
# Empty means allow all origins (default, for easy drop-in use).  Set this in
# production to restrict which sites can use your CAPTCHA.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "")

# How long a challenge (the widget->server step) is valid, in seconds.
CHALLENGE_TTL = int(os.environ.get("CHALLENGE_TTL", "180"))  # 3 minutes

# How long a verified token (the form->siteverify step) is valid, in seconds.
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "120"))  # 2 minutes

# Pre-generation pool sizing.
POOL_MIN = int(os.environ.get("POOL_MIN", "8"))
POOL_MAX = int(os.environ.get("POOL_MAX", "32"))
POOL_REFILL_INTERVAL = float(os.environ.get("POOL_REFILL_INTERVAL", "2.0"))

# CAPTCHA rendering parameters.
CANVAS_WIDTH = 960
CANVAS_HEIGHT = 400
ATLAS_WIDTH = 240
ATLAS_HEIGHT = CANVAS_HEIGHT
FONT_SIZE = 280
CODE_LENGTH = 4
VIDEO_FPS = 24
VIDEO_DURATION_SECONDS = 2.0
VIDEO_FRAMES = int(VIDEO_DURATION_SECONDS * VIDEO_FPS)
MOTION_SPEED = 40.0

# Rate limits (consumed by flask-limiter in app.py).
RATE_CHALLENGE = os.environ.get("RATE_CHALLENGE", "30 per minute")
RATE_VERIFY = os.environ.get("RATE_VERIFY", "30 per minute")
RATE_SITEVERIFY = os.environ.get("RATE_SITEVERIFY", "120 per minute")


def origin_allowed(origin: str | None) -> bool:
    """Return True if `origin` is allowed to use the widget.

    If ``ALLOWED_ORIGINS`` is empty (the default), any origin is allowed.
    Otherwise only the listed origins (comma-separated, exact match) are
    permitted.  Empty origins are always rejected.
    """
    if not origin:
        return False
    allowed = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    if not allowed:
        return True
    return origin in allowed
