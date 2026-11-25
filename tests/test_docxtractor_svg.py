import shutil
import subprocess
import tempfile
import unittest
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pypdfium2 as pdfium
from PIL import Image
from docling_core.types.doc import (
    BoundingBox, CoordOrigin, DoclingDocument, ImageRef, ProvenanceItem, Size,
)

import docxtractor


def image_stream(width, height):
    """A zlib-compressed RGB image: left half red, right half blue."""
    row = b"\xff\x00\x00" * (width // 2) + b"\x00\x00\xff" * (width - width // 2)
    data = zlib.compress(row * height)
    return (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(data)} >>\nstream\n").encode() + data + b"\nendstream"


def sample_pdf(*, raster=False, rotation=0, image_size=(1, 1), form=False,
               placement=b"50 0 0 50 30 40"):
    """A 250x150 pt page (crop box 200x100) with a red square or a 50 pt image at (30, 40).

    placement is the image's cm matrix, for positioning it elsewhere. With form=True the image is drawn at the unit square inside a form XObject,
    and the form is scaled into place, so the image's own matrix is form-relative.
    """
    if not raster:
        content = b"1 0 0 rg 30 40 50 50 re f"
        resources = b"/Resources << >>"
    elif form:
        content = b"q " + placement + b" cm /Fm0 Do Q"
        resources = b"/Resources << /XObject << /Fm0 5 0 R >> >>"
    else:
        content = b"q " + placement + b" cm /Im0 Do Q"
        resources = b"/Resources << /XObject << /Im0 5 0 R >> >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 250 150] "
         b"/CropBox [10 20 210 120] /Rotate " + str(rotation).encode() + b" "
         + resources + b" /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    if raster and form:
        inner = b"q 1 0 0 1 0 0 cm /Im0 Do Q"
        objects.append(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] "
            b"/Resources << /XObject << /Im0 6 0 R >> >> /Length " + str(len(inner)).encode()
            + b" >>\nstream\n" + inner + b"\nendstream"
        )
    if raster:
        objects.append(image_stream(*image_size))
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def document_with_picture(rotation=0, origin=CoordOrigin.TOPLEFT):
    document = DoclingDocument(name="sample")
    width, height = (200, 100) if rotation in (0, 180) else (100, 200)
    document.add_page(page_no=1, size=Size(width=width, height=height))
    bbox = BoundingBox(l=20, t=30, r=80, b=70)
    if origin == CoordOrigin.BOTTOMLEFT:
        bbox = bbox.to_bottom_left_origin(height)
    document.add_picture(
        image=ImageRef.from_pil(Image.new("RGB", (120, 80), "red"), dpi=144),
        prov=ProvenanceItem(page_no=1, bbox=bbox, charspan=(0, 0)),
    )
    return document


@unittest.skipUnless(shutil.which("pdftocairo"), "pdftocairo is required")
class PdfPictureExtractionTests(unittest.TestCase):
    def extract(self, pdf_data, document, directory):
        client = Mock()
        client.convert.return_value = SimpleNamespace(status="success", document=document, errors=[])
        return docxtractor.extract_document(
            pdf_data, filename="sample.pdf", output_dir=directory, client=client,
        )

    def test_pure_picture_crop_geometry_and_rotation(self):
        expected = {
            0: (30, 50, 90, 90),
            90: (40, 40, 80, 100),
            180: (130, 50, 190, 90),
            270: (140, 40, 180, 100),
        }
        real_run = subprocess.run
        for rotation in expected:
            for origin in (CoordOrigin.TOPLEFT, CoordOrigin.BOTTOMLEFT):
                with self.subTest(rotation=rotation, origin=origin), tempfile.TemporaryDirectory() as directory:
                    captured = []
                    def check_crop(args, **kwargs):
                        with pdfium.PdfDocument(args[-2]) as cropped:
                            page = cropped[0]
                            try:
                                captured.append(page.get_bbox())
                            finally:
                                page.close()
                        return real_run(args, **kwargs)
                    with patch.object(docxtractor.subprocess, "run", side_effect=check_crop):
                        manifest = self.extract(sample_pdf(rotation=rotation),
                                                document_with_picture(rotation, origin), directory)
                    self.assertEqual(captured, [expected[rotation]])
                    self.assertEqual(manifest["warnings"], [])
                    path = Path(manifest["output_directory"]) / manifest["elements"][0]["asset"]
                    self.assertEqual(path.suffix, ".svg")
                    root = ET.fromstring(path.read_bytes())
                    self.assertEqual([float(n) for n in root.attrib["viewBox"].split()],
                                     [0, 0, 60, 40])

    def asset(self, manifest):
        return Path(manifest["output_directory"]) / manifest["elements"][0]["asset"]

    def test_bitmap_region_is_rendered_from_the_pdf(self):
        # A 1x1 image is far below Docling's 144 DPI, so the render uses that minimum.
        # The region (user x 30-90) is wider than the (blue) image (x 30-80), so its
        # right sixth is white page rather than Docling's all-red crop.
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.extract(sample_pdf(raster=True), document_with_picture(), directory)
            path = self.asset(manifest)
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(manifest["elements"][0]["raster_assets"], [manifest["elements"][0]["asset"]])
            with Image.open(path) as image:
                self.assertEqual(image.size, (120, 80))
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((115, 40)), (255, 255, 255))
                self.assertEqual(image.getpixel((40, 40)), (0, 0, 255))
            self.assertEqual(manifest["warnings"], [])

    def test_render_matches_embedded_image_resolution_at_every_rotation(self):
        # The image covers the whole 200x100 pt crop box at 1000x500 px: 5 px/pt (360 DPI),
        # so the 60x40 pt region becomes 300x200 px wherever rotation puts it.
        for rotation in (0, 90, 180, 270):
            for origin in (CoordOrigin.TOPLEFT, CoordOrigin.BOTTOMLEFT):
                with self.subTest(rotation=rotation, origin=origin), \
                        tempfile.TemporaryDirectory() as directory:
                    pdf_data = sample_pdf(raster=True, rotation=rotation, image_size=(1000, 500),
                                          placement=b"200 0 0 100 10 20")
                    manifest = self.extract(pdf_data, document_with_picture(rotation, origin), directory)
                    with Image.open(self.asset(manifest)) as image:
                        self.assertEqual(image.size, (300, 200))
                        rendered = image.convert("RGB")
                    # Same pixels as rendering the whole displayed page at 5x and cropping the box.
                    with pdfium.PdfDocument(pdf_data) as pdf:
                        page = pdf[0]
                        try:
                            full = page.render(scale=5).to_pil().convert("RGB")
                        finally:
                            page.close()
                    expected = full.crop((100, 150, 400, 350))
                    difference = sum(abs(a - b) for p, q in zip(rendered.getdata(), expected.getdata())
                                     for a, b in zip(p, q)) / (300 * 200 * 3)
                    self.assertLess(difference, 2)

    def test_render_composes_form_matrices_for_nested_images(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_data = sample_pdf(raster=True, image_size=(250, 250), form=True)
            manifest = self.extract(pdf_data, document_with_picture(), directory)
            with Image.open(self.asset(manifest)) as image:
                self.assertEqual(image.size, (300, 200))

    def test_render_resolution_is_capped(self):
        # 1000 px across 50 pt would be 1440 DPI; it is capped at MAX_RENDER_DPI.
        with tempfile.TemporaryDirectory() as directory:
            pdf_data = sample_pdf(raster=True, image_size=(1000, 1000))
            manifest = self.extract(pdf_data, document_with_picture(), directory)
            scale = docxtractor.MAX_RENDER_DPI / 72
            with Image.open(self.asset(manifest)) as image:
                self.assertAlmostEqual(image.size[0], 60 * scale, delta=2)
                self.assertAlmostEqual(image.size[1], 40 * scale, delta=2)

    def test_render_failure_warns_and_falls_back_to_docling_crop(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                docxtractor, "_render_picture", side_effect=docxtractor.ExtractionError("boom")):
            manifest = self.extract(sample_pdf(raster=True), document_with_picture(), directory)
            with Image.open(self.asset(manifest)) as image:
                self.assertEqual(image.size, (120, 80))
                self.assertEqual(image.getpixel((115, 40)), (255, 0, 0))
            self.assertIn("Full-resolution rendering unavailable", manifest["warnings"][0])

    def test_conversion_failure_warns_and_falls_back_to_png(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(docxtractor.subprocess, "run", side_effect=FileNotFoundError("pdftocairo")):
            manifest = self.extract(sample_pdf(), document_with_picture(), directory)
            self.assertTrue(manifest["elements"][0]["asset"].endswith(".png"))
            self.assertIn("pdftocairo", manifest["warnings"][0])


class SvgClassificationTests(unittest.TestCase):
    def test_impure_svg_detection_and_invalid_xml(self):
        samples = (
            (b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>', True),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><image href="relative.png"/></svg>', False),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><style>url(DATA:image/PNG;base64,abc)</style></svg>', False),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "figure.svg"
            for content, pure in samples:
                path.write_bytes(content)
                self.assertEqual(docxtractor._pure_vector_svg(path), pure)
            path.write_bytes(b"<svg")
            with self.assertRaises(docxtractor.ExtractionError):
                docxtractor._pure_vector_svg(path)
