"""Extract a document's images and print the complexity and legibility scores the app stores.

Documents go through the same Docling extraction as an upload
(source_processing.extract), and each image asset is scored with the same
functions content.py uses when it inserts the source. Settings come from .env
(DOCLING_*, IMAGE_COMPLEXITY_*, DIAGRAM_LEGIBILITY_*), so results match the app
when the environment matches. No database or web server is needed.

    bin/python score_images.py paper.pdf -o extracted/
    bin/python score_images.py figure.png other.png
    bin/python score_images.py paper.pdf --json
"""

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import graphics  # noqa: E402  (loaded after .env so thresholds see its settings)
import source_processing as processing  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


def score(name: str, data: bytes, mimetype: str, raster: bytes | None = None) -> dict:
    complexity = graphics.complexity_threshold()
    legibility = graphics.legibility_threshold()
    result: dict = {"file": name, "mimetype": mimetype, "complexity": None, "legibility": None}
    # As in content.py: complexity is skipped for SVG; SVG legibility uses its raster crop.
    if mimetype != "image/svg+xml":
        result["complexity"] = processing.image_complexity_score(data)
    try:
        result["legibility"] = processing.image_legibility_score(data, mimetype, raster_data=raster)
    except ValueError as error:
        result["error"] = str(error)
    reasons = []
    if result["complexity"] is not None and result["complexity"] > complexity:
        reasons.append(f"complexity above {complexity:.1%}")
    if result["legibility"] is not None and result["legibility"] < legibility:
        reasons.append(f"legibility below {legibility:.1%}")
    result["suitable"] = not reasons and "error" not in result
    result["reasons"] = reasons
    return result


def from_document(path: Path, output: Path | None) -> list[dict]:
    processed = processing.extract(path.name, path.read_bytes())
    for warning in processed.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if output:
        output.mkdir(parents=True, exist_ok=True)
    results = []
    for asset in processed.assets:
        if not asset.mimetype.startswith("image/"):
            continue
        if output:
            (output / asset.filename).write_bytes(asset.data)
            if asset.description_image:
                (output / (Path(asset.filename).stem + ".raster.png")).write_bytes(asset.description_image)
        result = score(asset.filename, asset.data, asset.mimetype, asset.description_image)
        result["kind"] = asset.kind
        results.append(result)
    return results


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", type=Path, help="documents (PDF, DOCX, ...) or images")
    parser.add_argument("-o", "--output", type=Path, help="directory to save extracted images")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()

    results = []
    for path in args.files:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            data = path.read_bytes()
            mimetype = mimetypes.guess_type(path.name)[0] or "image/png"
            results.append(score(path.name, data, mimetype))
        else:
            try:
                results.extend(from_document(path, args.output))
            except (processing.ProcessingError, ValueError) as error:
                parser.exit(1, f"{path}: {error}\n")

    if args.json:
        print(json.dumps(results, indent=2))
        return
    width = max([len(r["file"]) for r in results] + [4])
    print(f"{'file':<{width}}  {'complexity':>10}  {'legibility':>10}  verdict")
    for r in results:
        verdict = "ok" if r["suitable"] else "; ".join(r["reasons"]) or r.get("error", "")
        print(f"{r['file']:<{width}}  {percent(r['complexity']):>10}  {percent(r['legibility']):>10}  {verdict}")


if __name__ == "__main__":
    main()
