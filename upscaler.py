"""Fidelity-checked image upscaling for poster figures.

Methods:
- lanczos: classical resampling, no AI; the baseline and the safe fallback.
- esrgan-general, esrgan, swin2sr: x4 super-resolution models loaded with spandrel
  from UPSCALE_MODELS_DIR (default cache/models). Other scales run the model and
  resample the x4 result (twice for scales above 4).
- vector: traces each quantized color with potrace and renders the stacked SVG with
  rsvg-convert; suited to flat-color diagrams.

Every result is checked unless check=False: the output is reduced back to the input
size and compared with SSIM, and every word OCR reads confidently in the input must
still be read in the output. A failed check falls back to lanczos unless strict=True.

CLI: python upscaler.py figure.png --print-width 12 --dpi 300 -o figure-12in.png
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import re
import subprocess
import threading
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
MODELS = {
    "esrgan-general": "realesr-general-x4v3.pth",
    "esrgan": "RealESRGAN_x4plus.pth",
    "swin2sr": "Swin2SR_RealworldSR_X4_64_BSRGAN_PSNR.pth",
}
METHODS = ("lanczos", *MODELS, "vector")
MAX_SCALE = 8.0
# Model activations at x4 output size grow fast: a 512 px tile needs over 1 GB per
# feature map in RealESRGAN_x4plus, which swaps an 8 GB machine to a standstill.
TILE = 256
TILE_PAD = 16
OCR_MIN_SCORE = 0.9
OCR_MAX_SIDE = 4000

logger = logging.getLogger(__name__)
_inference = threading.Lock()


class UpscaleError(RuntimeError):
    """The upscaler is not usable here (missing weights or tools)."""


@dataclass
class Upscaled:
    png: bytes
    method: str
    scale: float
    size: tuple[int, int]
    ssim: float | None = None
    missing_words: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "method": self.method, "scale": round(self.scale, 4), "size": list(self.size),
            "ssim": None if self.ssim is None else round(self.ssim, 4),
            "missing_words": self.missing_words, "warnings": self.warnings,
        }


def target_scale(width: int, print_width_in: float, dpi: int = 300) -> float:
    """Scale that makes an image `width` pixels wide print `print_width_in` at `dpi`."""
    if width <= 0 or print_width_in <= 0 or dpi <= 0:
        raise ValueError("Width, print width and dpi must be positive.")
    return print_width_in * dpi / width


def _setting(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _load(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("The image could not be read.") from error
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    return image.convert("RGBA" if has_alpha else "RGB")


def _flatten(image: Image.Image) -> Image.Image:
    if image.mode != "RGBA":
        return image.convert("RGB")
    background = Image.new("RGBA", image.size, "white")
    background.alpha_composite(image)
    return background.convert("RGB")


def _lanczos(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.resize(size, Image.Resampling.LANCZOS)


def _device() -> str:
    import torch

    configured = _setting("UPSCALE_DEVICE", "auto")
    if configured != "auto":
        return configured
    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=len(MODELS))
def _model(name: str, device: str) -> Any:
    import spandrel

    path = Path(_setting("UPSCALE_MODELS_DIR", str(BASE_DIR / "cache" / "models"))) / MODELS[name]
    if not path.is_file():
        raise UpscaleError(f"Model weights for {name} are missing: {path}")
    model = spandrel.ModelLoader().load_from_file(str(path))
    model.eval()
    return model.to(device)


def _padded(height: int, width: int, model: Any) -> tuple[int, int]:
    requirements = model.size_requirements
    sizes = []
    for size in (height, width):
        size = max(size, requirements.minimum)
        sizes.append(math.ceil(size / requirements.multiple_of) * requirements.multiple_of)
    if requirements.square:
        sizes = [max(sizes)] * 2
    return sizes[0], sizes[1]


def _run_model(model: Any, image: Image.Image) -> Image.Image:
    """Run a model over overlapping tiles; each tile keeps only its own area."""
    import torch

    array = np.asarray(image, dtype=np.float32) / 255
    height, width = array.shape[:2]
    factor = model.scale
    output = np.zeros((height * factor, width * factor, 3), np.float32)
    for top in range(0, height, TILE):
        for left in range(0, width, TILE):
            y0, x0 = max(top - TILE_PAD, 0), max(left - TILE_PAD, 0)
            y1, x1 = min(top + TILE + TILE_PAD, height), min(left + TILE + TILE_PAD, width)
            tile = array[y0:y1, x0:x1]
            padded_height, padded_width = _padded(*tile.shape[:2], model)
            tile = np.pad(tile, ((0, padded_height - tile.shape[0]), (0, padded_width - tile.shape[1]), (0, 0)),
                          mode="edge")
            tensor = torch.from_numpy(np.ascontiguousarray(tile)).permute(2, 0, 1)[None].to(model.device)
            with torch.inference_mode():
                result = model(tensor)[0].permute(1, 2, 0).clamp(0, 1).float().cpu().numpy()
            rows = (min(top + TILE, height) - top) * factor
            columns = (min(left + TILE, width) - left) * factor
            y, x = (top - y0) * factor, (left - x0) * factor
            output[top * factor:top * factor + rows, left * factor:left * factor + columns] = \
                result[y:y + rows, x:x + columns]
    return Image.fromarray((output * 255 + 0.5).astype(np.uint8))


def _super_resolve(name: str, image: Image.Image, size: tuple[int, int]) -> Image.Image:
    rgb = image.convert("RGB")
    device = _device()
    with _inference:
        while rgb.width < size[0] or rgb.height < size[1]:
            try:
                rgb = _run_model(_model(name, device), rgb)
            except (RuntimeError, NotImplementedError) as error:
                if device == "cpu" or isinstance(error, UpscaleError):
                    raise
                # Some architectures use operators MPS lacks; CPU is slower but complete.
                logger.warning("Upscaling with %s on %s failed (%s); retrying on CPU.", name, device, error)
                device = "cpu"
                rgb = _run_model(_model(name, device), rgb)
    result = _lanczos(rgb, size)
    if image.mode == "RGBA":
        # The models are RGB-only; transparency is smooth enough for resampling.
        result.putalpha(_lanczos(image.getchannel("A"), size))
    return result


def _tool(command: list[str], data: bytes, install: str) -> bytes:
    try:
        return subprocess.run(command, input=data, capture_output=True, check=True, timeout=300).stdout
    except FileNotFoundError as error:
        raise UpscaleError(f"{command[0]} is required for vector upscaling ({install}).") from error
    except subprocess.CalledProcessError as error:
        raise UpscaleError(f"{command[0]} failed: {error.stderr.decode(errors='replace')[:200]}") from error


def _vectorize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Trace each quantized color with potrace, stack the layers and render the SVG."""
    flat = _flatten(image)
    colors = int(_setting("UPSCALE_VECTOR_COLORS", "32"))
    quantized = flat.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    indices = np.asarray(quantized)
    palette = quantized.getpalette() or []
    counts = np.bincount(indices.ravel(), minlength=256)
    width, height = flat.size

    def color(index: int) -> str:
        return "#{:02x}{:02x}{:02x}".format(*palette[index * 3:index * 3 + 3])

    def luminance(index: int) -> float:
        red, green, blue = palette[index * 3:index * 3 + 3]
        return 0.299 * red + 0.587 * green + 0.114 * blue

    # Layers stack lightest to darkest so text and outlines end on top. Each layer also
    # covers every darker color; later layers paint over that, which leaves no seams
    # between neighbouring colors and keeps the darkest shapes exact.
    order = sorted((int(index) for index in np.nonzero(counts)[0]), key=luminance, reverse=True)
    layers = [f'<rect width="{width}" height="{height}" fill="{color(int(np.argmax(counts)))}"/>']
    for position, index in enumerate(order):
        mask = Image.fromarray(np.where(np.isin(indices, order[position:]), 0, 255).astype(np.uint8))
        bitmap = io.BytesIO()
        mask.convert("1").save(bitmap, format="PPM")
        traced = _tool(["potrace", "--backend", "svg", "--flat", "--color", color(index), "--output", "-"],
                       bitmap.getvalue(), "brew install potrace").decode()
        layers.extend(re.findall(r"<g\b.*?</g>", traced, re.DOTALL))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">{"".join(layers)}</svg>')
    rendered = _tool(["rsvg-convert", "--width", str(size[0]), "--height", str(size[1]), "--format", "png"],
                     svg.encode(), "brew install librsvg")
    result = Image.open(io.BytesIO(rendered)).convert("RGB")
    if image.mode == "RGBA":
        result.putalpha(_lanczos(image.getchannel("A"), size))
    return result


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


def ocr_words(image: Image.Image, min_score: float = 0.0) -> Counter[str]:
    """Normalized words of two or more characters that OCR reads in an image, counted.

    Isolated single characters (axis letters, variable names) are read too
    inconsistently to compare; the SSIM check still covers them.
    """
    flat = _flatten(image)
    if max(flat.size) > OCR_MAX_SIDE:
        flat.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE), Image.Resampling.LANCZOS)
    result = _ocr_engine()(np.asarray(flat))
    words: Counter[str] = Counter()
    for text, score in zip(result.txts or (), result.scores or ()):
        if score >= min_score:
            words.update(word for word in re.findall(r"[a-z0-9]{2,}", text.lower()))
    return words


def fidelity(source: Image.Image, result: Image.Image) -> tuple[float, list[str]]:
    """SSIM of the result reduced to the source size, and confidently read words it lost."""
    from skimage.metrics import structural_similarity

    reduced = _lanczos(_flatten(result), source.size)
    score = structural_similarity(
        np.asarray(_flatten(source).convert("L")), np.asarray(reduced.convert("L")), data_range=255,
    )
    missing = ocr_words(source, OCR_MIN_SCORE) - ocr_words(result)
    return float(score), sorted(missing.elements())


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _resize(method: str, image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if method == "lanczos":
        return _lanczos(image, size)
    if method == "vector":
        return _vectorize(image, size)
    return _super_resolve(method, image, size)


def upscale(
    data: bytes, *, scale: float | None = None, print_width_in: float | None = None, dpi: int = 300,
    method: str | None = None, check: bool = True, strict: bool = False,
) -> Upscaled:
    """Upscale PNG/JPEG bytes by `scale`, or to print `print_width_in` inches at `dpi`."""
    method = method or _setting("UPSCALE_METHOD", "esrgan-general")
    if method not in METHODS:
        raise ValueError(f"Unknown method {method!r}; expected one of {', '.join(METHODS)}.")
    if (scale is None) == (print_width_in is None):
        raise ValueError("Give exactly one of scale or print width.")
    image = _load(data)
    if scale is None:
        scale = target_scale(image.width, print_width_in, dpi)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Scale must be positive.")
    if scale <= 1:
        return Upscaled(_encode(image), "none", 1.0, image.size,
                        warnings=["The image already has enough pixels; it was not upscaled."])
    if scale > MAX_SCALE:
        raise ValueError(f"Scale {scale:.2f} exceeds the maximum of {MAX_SCALE:g}.")
    size = (round(image.width * scale), round(image.height * scale))
    max_pixels = int(_setting("UPSCALE_MAX_MEGAPIXELS", "120")) * 1_000_000
    if size[0] * size[1] > max_pixels:
        raise ValueError(f"The result would be {size[0]}x{size[1]} pixels, over the "
                         f"{max_pixels // 1_000_000} megapixel limit.")

    result = _resize(method, image, size)
    upscaled = Upscaled(b"", method, scale, size)
    if check:
        upscaled.ssim, upscaled.missing_words = fidelity(image, result)
        threshold = float(_setting("UPSCALE_MIN_SSIM", "0.93"))
        problems = []
        if upscaled.ssim < threshold:
            problems.append(f"SSIM {upscaled.ssim:.3f} is below {threshold:g}")
        if upscaled.missing_words:
            problems.append(f"text changed (lost: {', '.join(upscaled.missing_words)})")
        if problems:
            upscaled.warnings.append(f"{method} failed the fidelity check: {'; '.join(problems)}.")
            if method != "lanczos" and not strict:
                result = _lanczos(image, size)
                upscaled.method = "lanczos"
                upscaled.ssim, upscaled.missing_words = fidelity(image, result)
                upscaled.warnings.append("Fell back to lanczos resampling.")
    upscaled.png = _encode(result)
    return upscaled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    size = parser.add_mutually_exclusive_group(required=True)
    size.add_argument("--scale", type=float)
    size.add_argument("--print-width", type=float, help="final printed width in inches")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--no-check", action="store_true", help="skip the SSIM and OCR fidelity check")
    parser.add_argument("--strict", action="store_true", help="keep the method's result even if the check fails")
    args = parser.parse_args()
    result = upscale(args.source.read_bytes(), scale=args.scale, print_width_in=args.print_width,
                     dpi=args.dpi, method=args.method, check=not args.no_check, strict=args.strict)
    output = args.output or args.source.with_name(f"{args.source.stem}-upscaled.png")
    output.write_bytes(result.png)
    print(json.dumps({"output": str(output), **result.summary()}, indent=2))


if __name__ == "__main__":
    main()
