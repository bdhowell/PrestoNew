import io
import os
import shutil
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import numpy as np
from PIL import Image, ImageDraw

import upscaler
from routers import images


def png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def diagram(size: tuple[int, int] = (120, 80), mode: str = "RGBA") -> Image.Image:
    image = Image.new(mode, size, (255, 255, 255, 0) if mode == "RGBA" else "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, size[0] - 10, size[1] - 10), fill=(200, 230, 245, 255), outline=(0, 0, 0, 255), width=3)
    return image


class FakeModel:
    """Doubles pixels like a x2 model, with Swin-style size requirements."""

    scale = 2
    device = "cpu"
    size_requirements = SimpleNamespace(minimum=8, multiple_of=8, square=False)

    def __call__(self, tensor):
        assert tensor.shape[2] % 8 == 0 and tensor.shape[3] % 8 == 0
        return tensor.repeat_interleave(2, dim=2).repeat_interleave(2, dim=3)


class ScaleTests(unittest.TestCase):
    def test_target_scale_matches_print_size(self) -> None:
        self.assertAlmostEqual(upscaler.target_scale(1520, 12, 300), 3600 / 1520)

    def test_target_scale_rejects_non_positive_values(self) -> None:
        with self.assertRaises(ValueError):
            upscaler.target_scale(1520, 0, 300)


class UpscaleTests(unittest.TestCase):
    def test_lanczos_keeps_transparency_and_reaches_print_size(self) -> None:
        result = upscaler.upscale(png(diagram()), print_width_in=1, dpi=300, method="lanczos", check=False)
        image = Image.open(io.BytesIO(result.png))
        self.assertEqual(image.size, (300, 200))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(result.method, "lanczos")

    def test_enough_pixels_returns_the_image_unchanged(self) -> None:
        result = upscaler.upscale(png(diagram()), print_width_in=0.2, dpi=300, method="lanczos")
        self.assertEqual(result.method, "none")
        self.assertEqual(Image.open(io.BytesIO(result.png)).size, (120, 80))

    def test_requires_exactly_one_target(self) -> None:
        for arguments in ({}, {"scale": 2, "print_width_in": 4}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                upscaler.upscale(png(diagram()), method="lanczos", **arguments)

    def test_rejects_unknown_method_large_scale_and_large_output(self) -> None:
        with self.assertRaises(ValueError):
            upscaler.upscale(png(diagram()), scale=2, method="magic")
        with self.assertRaises(ValueError):
            upscaler.upscale(png(diagram()), scale=9, method="lanczos")
        with patch.dict(os.environ, {"UPSCALE_MAX_MEGAPIXELS": "0"}), self.assertRaises(ValueError):
            upscaler.upscale(png(diagram()), scale=2, method="lanczos")

    def test_rejects_unreadable_data(self) -> None:
        with self.assertRaises(ValueError):
            upscaler.upscale(b"not an image", scale=2, method="lanczos")

    def test_failed_check_falls_back_to_lanczos(self) -> None:
        def fake(name, image, size):
            return upscaler._lanczos(image, size)

        with patch.object(upscaler, "_super_resolve", side_effect=fake), \
                patch.object(upscaler, "fidelity", side_effect=[(0.5, ["norm"]), (0.99, [])]):
            result = upscaler.upscale(png(diagram()), scale=2, method="esrgan-general")
        self.assertEqual(result.method, "lanczos")
        self.assertEqual(result.ssim, 0.99)
        self.assertIn("esrgan-general failed the fidelity check", result.warnings[0])
        self.assertIn("lost: norm", result.warnings[0])

    def test_strict_keeps_the_method_after_a_failed_check(self) -> None:
        with patch.object(upscaler, "_super_resolve", side_effect=lambda name, image, size: image.resize(size)), \
                patch.object(upscaler, "fidelity", return_value=(0.5, [])):
            result = upscaler.upscale(png(diagram()), scale=2, method="esrgan-general", strict=True)
        self.assertEqual(result.method, "esrgan-general")
        self.assertEqual(len(result.warnings), 1)

    def test_ocr_words_ignore_single_characters_and_low_scores(self) -> None:
        engine = lambda image: SimpleNamespace(txts=("Add & Norm", "Q", "Feed"), scores=(0.99, 0.99, 0.5))
        with patch.object(upscaler, "_ocr_engine", return_value=engine):
            self.assertEqual(upscaler.ocr_words(diagram(), 0.9), Counter({"add": 1, "norm": 1}))


class TilingTests(unittest.TestCase):
    def test_tiles_stitch_into_the_whole_image(self) -> None:
        rng = np.random.default_rng(0)
        pixels = rng.integers(0, 256, (upscaler.TILE + 37, upscaler.TILE * 2 + 5, 3), dtype=np.uint8)
        result = np.asarray(upscaler._run_model(FakeModel(), Image.fromarray(pixels)))
        expected = pixels.repeat(2, axis=0).repeat(2, axis=1)
        self.assertEqual(result.shape, expected.shape)
        self.assertLessEqual(int(np.abs(result.astype(int) - expected).max()), 1)


@unittest.skipUnless(shutil.which("potrace") and shutil.which("rsvg-convert"), "potrace and rsvg-convert needed")
class VectorTests(unittest.TestCase):
    def test_vector_traces_a_diagram(self) -> None:
        result = upscaler.upscale(png(diagram(mode="RGB")), scale=4, method="vector", check=False)
        image = Image.open(io.BytesIO(result.png)).convert("RGB")
        self.assertEqual(image.size, (480, 320))
        self.assertEqual(image.getpixel((240, 160)), (200, 230, 245))
        self.assertEqual(image.getpixel((5, 5)), (255, 255, 255))
        # The dark outline must survive on top of the lighter fills.
        self.assertLess(max(image.getpixel((46, 160))), 40)


@unittest.skipUnless((upscaler.BASE_DIR / "cache" / "models" / upscaler.MODELS["esrgan-general"]).is_file(),
                     "model weights not downloaded")
class ModelTests(unittest.TestCase):
    def test_esrgan_general_upscales(self) -> None:
        result = upscaler.upscale(png(diagram()), scale=3, method="esrgan-general", check=False)
        self.assertEqual(Image.open(io.BytesIO(result.png)).size, (360, 240))


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        self.signed_in = True

        @app.middleware("http")
        async def identify(request: Request, call_next):
            request.state.user_id = 1 if self.signed_in else None
            return await call_next(request)

        app.include_router(images.router)
        self.client = TestClient(app)
        self.body = png(diagram())

    def post(self, query: str, **headers: str):
        return self.client.post(f"/images/upscale?{query}", content=self.body,
                                headers={"content-type": "application/octet-stream", **headers})

    def test_returns_png_and_result_headers(self) -> None:
        response = self.post("scale=2&method=lanczos&check=false")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-upscale-method"], "lanczos")
        self.assertEqual(response.headers["x-upscale-size"], "240x160")
        self.assertEqual(Image.open(io.BytesIO(response.content)).size, (240, 160))

    def test_requires_sign_in(self) -> None:
        self.signed_in = False
        self.assertEqual(self.post("scale=2&method=lanczos").status_code, 401)

    def test_rejects_other_content_types_and_cross_origin(self) -> None:
        response = self.client.post("/images/upscale?scale=2", content=self.body,
                                    headers={"content-type": "image/png"})
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.post("scale=2", origin="https://elsewhere.example").status_code, 403)

    def test_invalid_requests_are_422(self) -> None:
        self.assertEqual(self.post("scale=2&method=magic").status_code, 422)
        self.assertEqual(self.post("method=lanczos").status_code, 422)

    def test_missing_tools_are_503(self) -> None:
        with patch.object(images.upscaler, "upscale", side_effect=upscaler.UpscaleError("weights missing")):
            self.assertEqual(self.post("scale=2").status_code, 503)


if __name__ == "__main__":
    unittest.main()
