import shutil
import subprocess
import tempfile
import unittest
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


def sample_pdf(*, raster=False, rotation=0):
    content = (b"q 50 0 0 50 30 40 cm /Im0 Do Q" if raster else
               b"1 0 0 rg 30 40 50 50 re f")
    resources = b"/Resources << /XObject << /Im0 5 0 R >> >>" if raster else b"/Resources << >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 250 150] "
         b"/CropBox [10 20 210 120] /Rotate " + str(rotation).encode() + b" "
         + resources + b" /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    if raster:
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Filter /ASCIIHexDecode /Length 7 >>\nstream\nFF0000>\nendstream"
        )
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

    def test_bitmap_region_uses_original_png(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.extract(sample_pdf(raster=True), document_with_picture(), directory)
            path = Path(manifest["output_directory"]) / manifest["elements"][0]["asset"]
            self.assertEqual(path.suffix, ".png")
            with Image.open(path) as image:
                self.assertEqual(image.size, (120, 80))
            self.assertEqual(manifest["warnings"], [])

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
