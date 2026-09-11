"""Tray-icon images for the Windows front-end.

The Windows notification area has no text label — unlike the GNOME top bar or
the macOS menu bar, all it shows is a 16-ish-pixel icon. So the number *is*
the icon here: :func:`badge` draws the utilisation percentage into a rounded
tile whose colour carries the state, and the tooltip (built by the front-end)
holds the detail.

Pure Pillow, no Windows API — which is what makes it testable anywhere. The
front-end decides *what* to draw; this module only draws it.

Everything is best-effort: a missing font or an unreadable PNG degrades to a
plainer image, never an exception. A daemon must not die because a glyph is
absent.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Rendered at this size, then handed to pystray, which saves an .ico (Pillow
# derives the 16/24/32 variants from it) for Windows to scale into the tray.
ICON_SIZE = 32
# Corners and glyphs are drawn at 4x and downsampled: freetype antialiases
# text on its own, but a rounded rectangle drawn straight at 32 px has visibly
# stepped corners.
SUPERSAMPLE = 4

# Badge fills. Claude's terracotta for the normal state, then the usual
# warning ramp; grey covers "we have no fresh number" (rate-limited, error,
# signed out). Text is always white — white on any of these reads fine on both
# a light and a dark taskbar, which is why this needs no theme detection.
COLOR_NORMAL = (217, 119, 87)    # #D97757 — Claude
COLOR_WARN = (232, 163, 61)      # #E8A33D
COLOR_CRIT = (229, 72, 77)       # #E5484D
COLOR_STALE = (138, 138, 142)    # #8A8A8E
TEXT_COLOR = (255, 255, 255, 255)

# Attention fills, for the half-cycle where the icon blinks because a Claude
# terminal wants you back. Deliberately outside the terracotta/amber/red
# utilisation ramp: a blink must never read as "you're near a limit". Green
# says the turn is done, blue says a session is blocked on an answer.
COLOR_ATTENTION = {
    "done": (63, 185, 80),       # #3FB950
    "waiting": (79, 140, 255),   # #4F8CFF
}

WARN_AT = 80
CRIT_AT = 95

# First font that exists wins. The Segoe faces ship with Windows; the rest are
# fallbacks (Arial/Tahoma on older installs, DejaVu when this runs on Linux
# during development).
_FONT_CANDIDATES = (
    "segoeuib.ttf",
    "seguisb.ttf",
    "arialbd.ttf",
    "tahomabd.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)


def state_color(util: float, fresh: bool = True) -> tuple[int, int, int]:
    """Badge fill for a utilisation value; grey when the number isn't fresh."""
    if not fresh:
        return COLOR_STALE
    if util >= CRIT_AT:
        return COLOR_CRIT
    if util >= WARN_AT:
        return COLOR_WARN
    return COLOR_NORMAL


def badge_text(util: float) -> str:
    """Percentage as it appears on the icon — no '%', there's no room for it."""
    value = int(round(max(0.0, min(999.0, util))))
    return str(value)


@lru_cache(maxsize=64)
def _font(px: int) -> ImageFont.ImageFont:
    """Largest available bold face at ``px``, or Pillow's built-in default."""
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _fit_font(text: str, box_w: int, box_h: int) -> ImageFont.ImageFont:
    """Biggest font size whose ``text`` fits the box, floored at 6 px.

    Three digits ("100") need a much smaller size than one, so the size is
    searched rather than fixed — that keeps a lone "7" as large as it can be.
    """
    for px in range(box_h, 5, -1):
        font = _font(px)
        try:
            left, top, right, bottom = font.getbbox(text)
        except (AttributeError, OSError):
            return font
        if (right - left) <= box_w and (bottom - top) <= box_h:
            return font
    return _font(6)


def badge(
    text: str,
    color: tuple[int, int, int],
    size: int = ICON_SIZE,
) -> Image.Image:
    """A rounded tile in ``color`` with ``text`` centred on it, in white."""
    scale = size * SUPERSAMPLE
    img = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = max(1, scale // 32)
    radius = int(scale * 0.22)
    draw.rounded_rectangle(
        (margin, margin, scale - margin - 1, scale - margin - 1),
        radius=radius,
        fill=(*color, 255),
    )

    # Inner padding keeps the glyphs off the rounded corners — tighter for
    # three digits, which only ever means "100" and needs every pixel of
    # width it can get once Windows scales the tile down to 16 px.
    pad = int(scale * (0.08 if len(text) >= 3 else 0.14))
    font = _fit_font(text, scale - 2 * pad, scale - 2 * pad)
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    except (AttributeError, OSError):
        left = top = 0
        right = bottom = scale // 2
    x = (scale - (right - left)) // 2 - left
    y = (scale - (bottom - top)) // 2 - top
    draw.text((x, y), text, font=font, fill=TEXT_COLOR)

    return img.resize((size, size), Image.LANCZOS)


def logo(path: Path, size: int = ICON_SIZE) -> Image.Image | None:
    """Load one of the bundled PNGs, square and RGBA. ``None`` if unusable."""
    try:
        with Image.open(path) as src:
            return src.convert("RGBA").resize((size, size), Image.LANCZOS)
    except (OSError, ValueError):
        return None


def attention_color(kind: str) -> tuple[int, int, int]:
    """Blink fill for an attention state; unknown states read as "done"."""
    return COLOR_ATTENTION.get(kind, COLOR_ATTENTION["done"])


def attention_badge(
    text: str,
    kind: str,
    size: int = ICON_SIZE,
) -> Image.Image:
    """The blink frame: same glyph, attention fill.

    Keeping the text identical to the resting frame is the point — the number
    stays readable through the whole blink, only the colour pulses, so a
    glance still tells you where you are on your quota.
    """
    return badge(text, attention_color(kind), size)


def attention_dot(kind: str, size: int = ICON_SIZE) -> Image.Image:
    """Blink frame for icon-only mode: a filled tile, no glyph.

    The logo has no colour we can pulse without redrawing it, so the blink
    alternates the logo with this instead.
    """
    return badge(" ", attention_color(kind), size)


def fallback(size: int = ICON_SIZE) -> Image.Image:
    """Last-resort icon: a plain Claude-coloured "C".

    Reached only when every bundled PNG is missing *and* we have no number to
    show. The tray must always have something to display — an icon-less
    ``Shell_NotifyIcon`` is an invisible app.
    """
    return badge("C", COLOR_NORMAL, size)
