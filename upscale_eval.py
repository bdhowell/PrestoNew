"""Compare upscaling methods on a figure with a known answer.

The figure is reduced by --factor, each method restores it to the original size, and
the result is scored against the original: SSIM and PSNR, plus OCR word recall (the
share of words OCR reads confidently in the original that it reads in the result).
A comparison sheet shows the text-heavy regions at 100% for every method.

With --reference, the source is a real low-resolution input and the reference its true
high-resolution version, such as a vector figure rendered from the PDF at two dpis.

    python upscale_eval.py .docs/1706.03762v7-images/figure1-transformer.png --output eval/figure1
    python upscale_eval.py figure4-75dpi.png --reference figure4-300dpi.png --output eval/figure4
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import upscaler

CROP = 280
LABEL = 28


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(upscaler._flatten(image).convert("L"))


def score(original: Image.Image, result: Image.Image, words: dict[str, int]) -> dict[str, float]:
    from skimage.metrics import peak_signal_noise_ratio, structural_similarity

    expected, actual = _gray(original), _gray(result)
    found = upscaler.ocr_words(result)
    total = sum(words.values())
    recalled = sum(min(count, found[word]) for word, count in words.items())
    return {
        "ssim": round(float(structural_similarity(expected, actual, data_range=255)), 4),
        "psnr": round(float(peak_signal_noise_ratio(expected, actual, data_range=255)), 2),
        "word_recall": round(recalled / total, 3) if total else 1.0,
        "lost_words": sorted(set(words) - set(found)),
    }


def text_regions(image: Image.Image, count: int) -> list[tuple[int, int]]:
    """Centers of the longest confidently read text lines, kept apart from each other."""
    result = upscaler._ocr_engine()(np.asarray(upscaler._flatten(image)))
    lines = sorted(
        ((len(text), box) for text, box, confidence in zip(result.txts or (), result.boxes if result.boxes is not None else (), result.scores or ())
         if confidence >= upscaler.OCR_MIN_SCORE),
        key=lambda line: -line[0],
    )
    centers: list[tuple[int, int]] = []
    for _, box in lines:
        x, y = (int(value) for value in np.asarray(box).mean(axis=0))
        if all(abs(x - cx) > CROP or abs(y - cy) > CROP for cx, cy in centers):
            centers.append((x, y))
        if len(centers) == count:
            break
    return centers or [(image.width // 2, image.height // 2)]


def sheet(panels: dict[str, Image.Image], centers: list[tuple[int, int]], size: tuple[int, int]) -> Image.Image:
    font = ImageFont.load_default(size=18)
    names = list(panels)
    canvas = Image.new("RGB", (CROP * len(names), LABEL + CROP * len(centers)), "white")
    draw = ImageDraw.Draw(canvas)
    for column, name in enumerate(names):
        draw.text((column * CROP + 8, 4), name, fill="black", font=font)
        flat = upscaler._flatten(panels[name])
        for row, (x, y) in enumerate(centers):
            left = min(max(x - CROP // 2, 0), size[0] - CROP)
            top = min(max(y - CROP // 2, 0), size[1] - CROP)
            canvas.paste(flat.crop((left, top, left + CROP, top + CROP)), (column * CROP, LABEL + row * CROP))
    for column in range(1, len(names)):
        draw.line([(column * CROP, 0), (column * CROP, canvas.height)], fill="#bbbbbb")
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--factor", type=int, default=4, help="reduction factor to restore (default 4)")
    parser.add_argument("--reference", type=Path,
                        help="true high-resolution version of source (e.g. a vector figure rendered at "
                             "higher dpi); source is then used as the input instead of being reduced")
    parser.add_argument("--methods", nargs="+", choices=upscaler.METHODS, default=list(upscaler.METHODS))
    parser.add_argument("--crops", type=int, default=3, help="text regions on the comparison sheet")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    if args.reference:
        original = upscaler._load(args.reference.read_bytes())
        reduced = upscaler._load(args.source.read_bytes())
        factor = original.width / reduced.width
        if abs(original.height / reduced.height - factor) > 0.02 * factor:
            parser.error("The reference must have the same aspect ratio as the source.")
    else:
        original = upscaler._load(args.source.read_bytes())
        factor = args.factor
        reduced = upscaler._lanczos(original, (original.width // factor, original.height // factor))
    reduced.save(args.output / "reduced.png")
    words = upscaler.ocr_words(original, upscaler.OCR_MIN_SCORE)
    results = {"source": str(args.source), "reference": str(args.reference or args.source),
               "size": list(original.size), "factor": round(factor, 3),
               "reference_words": sum(words.values()), "methods": {}}
    # Nearest-neighbour enlargement shows what the methods were given to work with.
    panels = {"original": original, "input": reduced.resize(original.size, Image.Resampling.NEAREST)}
    for method in args.methods:
        started = time.perf_counter()
        try:
            restored = upscaler._resize(method, reduced, original.size)
        except upscaler.UpscaleError as error:
            results["methods"][method] = {"error": str(error)}
            print(f"{method:15} skipped: {error}")
            continue
        seconds = round(time.perf_counter() - started, 1)
        restored.save(args.output / f"{method}.png")
        panels[method] = restored
        results["methods"][method] = {**score(original, restored, words), "seconds": seconds}
        metrics = results["methods"][method]
        print(f"{method:15} SSIM {metrics['ssim']:.4f}  PSNR {metrics['psnr']:5.2f}  "
              f"words {metrics['word_recall']:.0%}  {seconds:6.1f}s  lost: {', '.join(metrics['lost_words']) or '-'}")
    sheet(panels, text_regions(original, args.crops), original.size).save(args.output / "sheet.png")
    (args.output / "results.json").write_text(json.dumps(results, indent=2))
    print(f"Wrote {args.output / 'sheet.png'} and {args.output / 'results.json'}")


if __name__ == "__main__":
    main()
