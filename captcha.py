"""Ghost Font CAPTCHA rendering.

This module is intentionally framework-agnostic: it knows nothing about Flask,
sessions, or HTTP.  It exposes two pure helpers:

* :func:`generate_code` - produce a human-friendly random code.
* :func:`render_video_bytes` - render the ghost-font effect as an MP4 byte
  string for a given code.

Keeping it isolated makes it trivial to pre-generate videos offline (see
``pool.py``) and to unit-test the renderer.
"""

from __future__ import annotations

import io
import secrets

import imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import (
    ATLAS_HEIGHT,
    ATLAS_WIDTH,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CODE_LENGTH,
    FONT_SIZE,
    MOTION_SPEED,
    VIDEO_DURATION_SECONDS,
    VIDEO_FPS,
    VIDEO_FRAMES,
)

# Characters that are easy to read and hard to confuse (no 0/O, 1/I).
_READABLE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code(length: int = CODE_LENGTH) -> str:
    """Return a random CAPTCHA code using easily-readable characters."""
    return "".join(secrets.choice(_READABLE_CHARS) for _ in range(length))


# --------------------------------------------------------------------------- #
# Font loading
# --------------------------------------------------------------------------- #
_FONT_CANDIDATES = (
    "arialbd.ttf",
    "Arial Black.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont:
    """Return a bold font, falling back to the default if necessary."""
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


_FONT = _load_font(FONT_SIZE)


# --------------------------------------------------------------------------- #
# Frame rendering
# --------------------------------------------------------------------------- #
def _build_text_mask(code: str) -> np.ndarray:
    """Render `code` as a centred binary mask."""
    img = Image.new("L", (CANVAS_WIDTH, CANVAS_HEIGHT), 0)
    draw = ImageDraw.Draw(img)
    draw.text(
        (CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2 + 24),
        code,
        font=_FONT,
        fill=255,
        anchor="mm",
    )
    return np.array(img, dtype=bool)


def _build_noise_atlas() -> np.ndarray:
    """Create the sparse-dot noise atlas used for both layers."""
    atlas = np.full((ATLAS_HEIGHT, ATLAS_WIDTH, 3), 0, dtype=np.uint8)
    cols = ATLAS_WIDTH // 5
    rows = ATLAS_HEIGHT // 5
    cell_h = ATLAS_HEIGHT // rows
    cell_w = ATLAS_WIDTH // cols

    rng = np.random.default_rng()
    for y in range(rows):
        for x in range(cols):
            if rng.random() > 0.25:
                continue
            py = y * cell_h + 1
            px = x * cell_w + 1
            atlas[py : py + 3, px : px + 3] = 255
    return atlas


def _render_layer(atlas: np.ndarray, direction: int, offset_x: int, travel: float) -> np.ndarray:
    """Tile the noise atlas and crop it to the canvas with the given motion."""
    travel_px = int((direction * travel) % ATLAS_HEIGHT)
    base_x = offset_x % ATLAS_WIDTH

    reps_x = (CANVAS_WIDTH // ATLAS_WIDTH) + 3
    reps_y = (CANVAS_HEIGHT // ATLAS_HEIGHT) + 3
    tiled = np.tile(atlas, (reps_y, reps_x, 1))

    return tiled[
        travel_px : travel_px + CANVAS_HEIGHT,
        base_x : base_x + CANVAS_WIDTH,
    ]


def render_video_bytes(code: str) -> bytes:
    """Render the ghost-font CAPTCHA for `code` and return raw MP4 bytes."""
    mask = _build_text_mask(code)
    atlas = _build_noise_atlas()

    frames = []
    for i in range(VIDEO_FRAMES):
        travel = (MOTION_SPEED * i / VIDEO_FPS) % CANVAS_HEIGHT
        bg = _render_layer(atlas, direction=1, offset_x=0, travel=travel)
        sig = _render_layer(atlas, direction=-1, offset_x=ATLAS_WIDTH // 2, travel=travel)
        frame = np.where(mask[..., None], sig, bg).astype(np.uint8)
        frames.append(frame)

    out = io.BytesIO()
    writer = imageio.get_writer(
        out,
        format="mp4",
        fps=VIDEO_FPS,
        codec="libx264",
        macro_block_size=None,
        quality=8,
    )
    for frame in frames:
        writer.append_data(frame)
    writer.close()
    out.seek(0)
    return out.read()
