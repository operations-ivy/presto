"""Photos to the Presto's 480x480 screen, keeping as much detail as possible.

    decode (HEIC/JPEG/PNG; rotation applied)
    -> colour to sRGB (the photo's ICC profile through LittleCMS, or the exact
       Display P3 -> sRGB matrix when HEIC says P3 by NCLX code)
    -> linear light, float32
    -> crop square (chosen by detail, or the user's) and Lanczos resize, in linear light
    -> back to sRGB, a light unsharp mask tuned for 480 px
    -> Floyd-Steinberg dither to RGB565 (what the Presto's framebuffer holds)

Outputs: the RGB565 frame (raw, big-endian), a high-quality JPEG, a PNG of
exactly what the RGB565 frame looks like (the upload page's preview), and a
~1600 px master for re-cropping after the original is gone.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pillow_heif
from PIL import Image, ImageCms, ImageOps

pillow_heif.register_heif_opener()

SIZE = 480
MASTER = 1600
MAX_PIXELS = 100_000_000  # refuse absurd images (decompression bombs)
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

# Display P3 (D65) -> sRGB, linear light (both D65, so no adaptation).
P3_TO_SRGB = np.array(
    [
        [1.2249401, -0.2249404, 0.0],
        [-0.0420569, 1.0420571, 0.0],
        [-0.0196376, -0.0786361, 1.0982735],
    ],
    dtype=np.float32,
)
NCLX_P3 = 12  # ITU-T H.273 colour_primaries: SMPTE EG 432-1 (P3-D65)

_SRGB = ImageCms.createProfile("sRGB")


class Rejected(ValueError):
    """Not something we'll convert (not an image, too big, ...)."""


@dataclass
class Source:
    format: str  # HEIF, JPEG, PNG, ...
    width: int
    height: int
    colour: str  # "Display P3 (ICC)", "sRGB", "P3 (NCLX)", ...
    bit_depth: int


# --- colour ---------------------------------------------------------------
def srgb_to_linear(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(v):
    v = np.clip(v, 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055).astype(np.float32)


def _profile_name(icc: bytes) -> str:
    try:
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() or "ICC"
    except Exception:
        return "ICC (unreadable)"


def decode(data: bytes) -> tuple[np.ndarray, Source]:
    """Bytes -> (linear-light sRGB float32 HxWx3, what it was)."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Image.DecompressionBombError as e:
        raise Rejected(f"image too large: {e}") from e
    except Exception as e:
        raise Rejected(f"not an image we can read: {e}") from e
    fmt = im.format or "?"
    depth = int(im.info.get("bit_depth", 8))
    im = ImageOps.exif_transpose(im)  # HEIC: already rotated, tag reset to 1 (pillow-heif)
    w, h = im.size
    if w * h > MAX_PIXELS:
        raise Rejected("image too large")
    icc = im.info.get("icc_profile")
    nclx = im.info.get("nclx_profile") or {}
    if im.mode not in ("RGB", "RGBA", "L", "LA", "P", "I;16", "RGB;16"):
        im = im.convert("RGB")
    if im.mode in ("RGBA", "LA", "P", "L"):
        im = im.convert("RGB")
    if icc:
        name = _profile_name(icc)
        if "srgb" not in name.lower():
            im = ImageCms.profileToProfile(
                im,
                ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                _SRGB,
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                outputMode="RGB",
                flags=ImageCms.Flags.BLACKPOINTCOMPENSATION,
            )
        rgb = np.asarray(im, dtype=np.float32) / 255.0
        lin = srgb_to_linear(rgb)
        colour = f"{name} (ICC)"
    else:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
        lin = srgb_to_linear(rgb)  # P3 uses the sRGB transfer curve too
        if nclx.get("color_primaries") == NCLX_P3:
            lin = np.clip(lin @ P3_TO_SRGB.T, 0.0, 1.0)
            colour = "Display P3 (NCLX)"
        else:
            colour = "sRGB (assumed)"
    return lin, Source(fmt, w, h, colour, depth)


# --- crop -----------------------------------------------------------------
def auto_crop(lin: np.ndarray) -> tuple[float, float, float]:
    """The square (cx, cy, size; fractions of width/height and of the short side)
    holding the most detail, with a pull toward the centre. A stand-in for
    subject detection: detail (edges) is where subjects usually are."""
    h, w = lin.shape[:2]
    lum = lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    k = max(1, max(h, w) // 256)  # work on a ~256 px thumbnail
    small = lum[: h - h % k, : w - w % k].reshape(h // k, k, w // k, k).mean(axis=(1, 3))
    gy, gx = np.gradient(small)
    energy = np.hypot(gx, gy)
    sh, sw = small.shape
    side = min(sh, sw)
    if sh == sw:
        return 0.5, 0.5, 1.0
    cols = energy.sum(axis=0) if sw > sh else energy.sum(axis=1)
    length = sw if sw > sh else sh
    sums = np.convolve(cols, np.ones(side), "valid")  # energy of each window position
    centre = (length - side) / 2
    pull = 1.0 - 0.25 * (np.abs(np.arange(len(sums)) - centre) / max(1.0, centre))
    best = int(np.argmax(sums * pull))
    mid = (best + side / 2) / length
    return (mid, 0.5, 1.0) if sw > sh else (0.5, mid, 1.0)


def crop_box(w: int, h: int, crop: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) in source pixels for a (cx, cy, size) crop, kept inside."""
    cx, cy, size = crop
    side = max(8.0, min(w, h) * max(0.05, min(1.0, size)))
    left = min(max(0.0, cx * w - side / 2), w - side)
    top = min(max(0.0, cy * h - side / 2), h - side)
    return left, top, left + side, top + side


# --- resize, sharpen, dither --------------------------------------------
def resize_linear(lin: np.ndarray, box, out: int) -> np.ndarray:
    """Lanczos in linear light, one float channel at a time (Pillow's F mode)."""
    chans = []
    for c in range(3):
        f = Image.fromarray(np.ascontiguousarray(lin[..., c]), "F")
        chans.append(np.asarray(f.resize((out, out), Image.LANCZOS, box=box, reducing_gap=3.0)))
    return np.clip(np.stack(chans, axis=-1), 0.0, 1.0)


def _gauss_kernel(sigma: float) -> np.ndarray:
    r = max(1, int(3 * sigma + 0.5))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2 * sigma * sigma))
    return k / k.sum()


def unsharp(img: np.ndarray, sigma=0.6, amount=0.35) -> np.ndarray:
    """A light unsharp mask on sRGB-encoded values (separable Gaussian, edges clamped)."""
    k = _gauss_kernel(sigma)
    r = len(k) // 2
    pad = np.pad(img, ((r, r), (r, r), (0, 0)), mode="edge")
    blur = sum(k[i] * pad[i : i + img.shape[0], r : r + img.shape[1]] for i in range(len(k)))
    pad = np.pad(blur, ((0, 0), (r, r), (0, 0)), mode="edge")
    blur = sum(k[i] * pad[:, i : i + img.shape[1]] for i in range(len(k)))
    return np.clip(img + amount * (img - blur), 0.0, 1.0)


def dither_rgb565(img: np.ndarray) -> np.ndarray:
    """Floyd-Steinberg to 5/6/5 bits; returns the uint16 RGB565 frame (HxW).

    Each channel on its own, in plain Python lists: the error diffusion is
    sequential, and per-pixel numpy calls were 10x slower (2.9 s vs 0.29 s a
    frame on the laptop, same output)."""
    h, w = img.shape[:2]
    planes = []
    for c, top in enumerate((31, 63, 31)):
        cur = (img[..., c].astype(np.float64) * top).tolist()
        for y in range(h):
            row = cur[y]
            nxt = cur[y + 1] if y + 1 < h else None
            for x in range(w):
                old = row[x]
                q = int(old + 0.5) if old > 0 else 0
                if q > top:
                    q = top
                row[x] = q
                e = old - q
                if x + 1 < w:
                    row[x + 1] += e * 0.4375
                if nxt is not None:
                    if x:
                        nxt[x - 1] += e * 0.1875
                    nxt[x] += e * 0.3125
                    if x + 1 < w:
                        nxt[x + 1] += e * 0.0625
        planes.append(np.array(cur, dtype=np.uint16))
    return (planes[0] << 11) | (planes[1] << 5) | planes[2]


def rgb565_to_rgb8(frame: np.ndarray) -> np.ndarray:
    r = (frame >> 11) & 31
    g = (frame >> 5) & 63
    b = frame & 31
    return np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31], axis=-1).astype(np.uint8)


# --- the whole thing -----------------------------------------------------
@dataclass
class Rendered:
    rgb565: bytes  # 480*480*2, big-endian
    jpeg: bytes  # 480x480, baseline, quality 95, 4:4:4
    preview_png: bytes  # exactly the RGB565 frame, as the Presto shows it
    crop: tuple[float, float, float]


def render(lin: np.ndarray, crop: tuple[float, float, float] | None = None) -> Rendered:
    h, w = lin.shape[:2]
    crop = crop or auto_crop(lin)
    small = resize_linear(lin, crop_box(w, h, crop), SIZE)
    enc = unsharp(linear_to_srgb(small))
    frame = dither_rgb565(enc)
    jpg = io.BytesIO()
    Image.fromarray((enc * 255 + 0.5).astype(np.uint8), "RGB").save(
        jpg, "JPEG", quality=95, subsampling=0, progressive=False, optimize=True
    )
    png = io.BytesIO()
    Image.fromarray(rgb565_to_rgb8(frame), "RGB").save(png, "PNG", optimize=True)
    return Rendered(frame.astype(">u2").tobytes(), jpg.getvalue(), png.getvalue(), crop)


def master(lin: np.ndarray) -> bytes:
    """A ~1600 px sRGB JPEG (quality 95, 4:4:4): enough to re-crop at full quality."""
    h, w = lin.shape[:2]
    scale = min(1.0, MASTER / max(w, h))
    mw, mh = max(1, round(w * scale)), max(1, round(h * scale))
    chans = [
        np.asarray(Image.fromarray(np.ascontiguousarray(lin[..., c]), "F").resize((mw, mh), Image.LANCZOS))
        for c in range(3)
    ]
    enc = linear_to_srgb(np.clip(np.stack(chans, axis=-1), 0, 1))
    out = io.BytesIO()
    Image.fromarray((enc * 255 + 0.5).astype(np.uint8), "RGB").save(out, "JPEG", quality=95, subsampling=0)
    return out.getvalue()
