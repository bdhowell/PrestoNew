"""Scores for extracted figures depend on their physical size, not their pixel resolution."""

import os
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import source_processing as processing


def drawing(dpi: float) -> bytes:
    """The same 3 x 1 inch diagram (text and lines) rasterized at the given DPI."""
    scale = dpi / 144
    width, height = round(3 * dpi), round(dpi)
    image = np.full((height, width), 255, np.uint8)
    for row, text in enumerate(("Encoder output", "softmax(QK^T)V")):
        cv2.putText(image, text, (round(10 * scale), round((50 + 60 * row) * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1 * scale, 0, max(1, round(2 * scale)), cv2.LINE_AA)
    cv2.rectangle(image, (round(5 * scale), round(5 * scale)),
                  (width - round(5 * scale), height - round(5 * scale)), 0, max(1, round(scale)))
    return cv2.imencode(".png", image)[1].tobytes()


WIDTH_MM = 3 * 25.4


class PhysicalScoringTests(unittest.TestCase):
    def test_complexity_does_not_depend_on_extraction_resolution(self):
        low, high = drawing(144), drawing(500)
        reference = processing.image_complexity_score(low, physical_width_mm=WIDTH_MM)
        self.assertAlmostEqual(
            processing.image_complexity_score(high, physical_width_mm=WIDTH_MM), reference, delta=reference * 0.1)
        # Without a physical size the pixels decide, as before.
        self.assertLess(processing.image_complexity_score(high), reference * 0.7)

    def test_legibility_is_judged_at_the_magnified_source_size(self):
        low, high = drawing(144), drawing(500)
        with patch.dict(os.environ, {"DIAGRAM_LEGIBILITY_SOURCE_MAGNIFICATION": "1.5"}):
            low_score = processing.image_legibility_score(low, "image/png", physical_width_mm=WIDTH_MM)
            high_score = processing.image_legibility_score(high, "image/png", physical_width_mm=WIDTH_MM)
            # The old behavior: a 144 DPI image shown at 96 DPI is also 1.5x its source size.
            self.assertAlmostEqual(low_score, processing.image_legibility_score(low, "image/png"))
        self.assertAlmostEqual(high_score, low_score, delta=0.1)
        with patch.dict(os.environ, {"DIAGRAM_LEGIBILITY_SOURCE_MAGNIFICATION": "0.2"}):
            smaller = processing.image_legibility_score(high, "image/png", physical_width_mm=WIDTH_MM)
        self.assertLess(smaller, high_score)

    def test_invalid_magnification_and_width_are_rejected(self):
        with patch.dict(os.environ, {"DIAGRAM_LEGIBILITY_SOURCE_MAGNIFICATION": "0"}), \
                self.assertRaises(processing.ProcessingError):
            processing.image_legibility_score(drawing(144), "image/png", physical_width_mm=WIDTH_MM)
        with self.assertRaises(ValueError):
            processing.image_legibility_score(drawing(144), "image/png", physical_width_mm=float("nan"))


if __name__ == "__main__":
    unittest.main()
