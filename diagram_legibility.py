"""Estimate minimum legible physical sizes for raster scientific diagrams.

This is a configurable *screening heuristic*, NOT a clinically validated model of
human visual perception. Detection thresholds (angular dimensions and contrast)
should be calibrated against observers and the intended print/display conditions.

Requires: numpy, opencv-python; Pillow is optional (for callers' image loading).

Coordinates use original image pixels, with boxes (x, y, width, height).
All physical dimensions/distances are millimeters. Angles are arcminutes.

Typical use::

    from diagram_legibility import DiagramAnalyzer, ViewingParameters, FeatureSpec
    analyzer = DiagramAnalyzer.from_file('figure.png')
    analyzer.add_feature(FeatureSpec('axis-labels', (40, 450, 100, 18),
                                     category='text', critical=True))
    result = analyzer.minimum_size(ViewingParameters(distance_mm=914.4))
    print(result.to_dict())

When the image has already been analyzed, re-use the DiagramAnalyzer across
many size and viewing-parameter queries. For reliable text segmentation, pass
text regions as manual FeatureSpec objects (OCR-free analysis is deliberately
not represented as accurate text detection).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
from typing import Any, Literal, Mapping

import cv2
import numpy as np

Category = Literal['text', 'line', 'symbol', 'detail']


def _positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')


def _mm_to_angle(size_mm: float, distance_mm: float) -> float:
    return math.degrees(2 * math.atan2(size_mm, 2 * distance_mm)) * 60


@dataclass(frozen=True)
class Thresholds:
    """Required angular sizes in arcminutes (heuristic starting values).

    ``stroke`` is for the width of contrast-defined strokes. ``extent`` is
    for the shorter dimension of a component, except for text, where the box
    height is used. Text should preferably be supplied through annotations.
    """
    stroke: Mapping[str, float] = field(default_factory=lambda: {
        'text': 1.0, 'line': 0.8, 'symbol': 0.8, 'detail': 0.8,
    })
    extent: Mapping[str, float] = field(default_factory=lambda: {
        'text': 16.0, 'line': 0.0, 'symbol': 4.0, 'detail': 2.0,
    })

    def __post_init__(self) -> None:
        for table in (self.stroke, self.extent):
            for category in ('text', 'line', 'symbol', 'detail'):
                if category not in table:
                    raise ValueError(f'missing threshold for {category}')
                value = float(table[category])
                if not math.isfinite(value) or value < 0:
                    raise ValueError('angle thresholds must be finite and nonnegative')


_PRESETS = {'minimum': 0.8, 'standard': 1.0,
            'comfortable': 1.4, 'high': 1.8}


@dataclass(frozen=True)
class ViewingParameters:
    distance_mm: float = 914.4
    legibility_level: Literal['minimum', 'standard', 'comfortable', 'high'] = 'comfortable'
    visual_acuity: float = 1.0  # relative to 20/20, smaller means poorer acuity
    required_feature_fraction: float = 0.95
    critical_features_required: bool = True
    contrast_reference: float = 0.45  # Weber-like normalized image contrast
    contrast_floor: float = 0.04
    max_contrast_penalty: float = 4.0
    thresholds: Thresholds = field(default_factory=Thresholds)

    def __post_init__(self) -> None:
        _positive('distance_mm', self.distance_mm)
        _positive('visual_acuity', self.visual_acuity)
        if self.legibility_level not in _PRESETS:
            raise ValueError('unknown legibility level')
        if not math.isfinite(self.required_feature_fraction) or not (
            0 < self.required_feature_fraction <= 1
        ):
            raise ValueError('required_feature_fraction must be in (0, 1]')
        for name, value in (
            ('contrast_reference', self.contrast_reference),
            ('contrast_floor', self.contrast_floor),
            ('max_contrast_penalty', self.max_contrast_penalty),
        ):
            _positive(name, value)


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    bbox: tuple[int, int, int, int]
    category: Category = 'detail'
    critical: bool = False
    stroke_px: float | None = None
    extent_px: float | None = None
    contrast: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError('feature name cannot be empty')
        if self.category not in ('text', 'line', 'symbol', 'detail'):
            raise ValueError('unknown feature category')
        if len(self.bbox) != 4 or self.bbox[2] <= 0 or self.bbox[3] <= 0:
            raise ValueError('feature bbox must have positive width and height')
        for name, value in (('stroke_px', self.stroke_px), ('extent_px', self.extent_px)):
            if value is not None:
                _positive(name, value)
        if self.contrast is not None and (
            not math.isfinite(self.contrast) or not 0 <= self.contrast <= 1
        ):
            raise ValueError('contrast must be finite and between 0 and 1')


@dataclass(frozen=True)
class FeatureVisibility:
    name: str
    visibility_margin: float
    visible: bool
    critical: bool


@dataclass(frozen=True)
class LegibilityEvaluation:
    score: float
    passes: bool
    visible_fraction: float
    critical_features_visible: bool
    features: tuple[FeatureVisibility, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'score': self.score,
            'passes': self.passes,
            'visible_fraction': self.visible_fraction,
            'critical_features_visible': self.critical_features_visible,
            'features': [asdict(feature) for feature in self.features],
        }


@dataclass(frozen=True)
class MinimumSizeResult:
    feasible: bool
    fits_constraints: bool
    minimum_width_mm: float
    minimum_height_mm: float
    scale_factor: float
    legibility_score: float
    limiting_features: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DiagramAnalyzer:
    """Analyze image features once and evaluate their legibility at any size."""

    def __init__(
        self,
        image: np.ndarray,
        *,
        include_auto: bool = True,
        thresholds: Thresholds | None = None,
    ) -> None:
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise ValueError('image must be a non-empty NumPy array')
        if image.ndim == 2:
            gray = image
        elif image.ndim == 3 and image.shape[2] in (3, 4):
            conversion = cv2.COLOR_BGR2GRAY if image.shape[2] == 3 else cv2.COLOR_BGRA2GRAY
            gray = cv2.cvtColor(image, conversion)
        else:
            raise ValueError('image must be grayscale, BGR, or BGRA')
        if gray.dtype != np.uint8:
            minimum, maximum = float(gray.min()), float(gray.max())
            if maximum <= minimum:
                gray = np.zeros(gray.shape, dtype=np.uint8)
            else:
                gray = np.clip(
                    (gray.astype(np.float64) - minimum) * (255 / (maximum - minimum)),
                    0, 255,
                ).astype(np.uint8)
        self.image = gray
        self.height, self.width = gray.shape
        if self.width <= 0 or self.height <= 0:
            raise ValueError('image dimensions must be positive')
        self.thresholds = thresholds or Thresholds()
        self.features: list[FeatureSpec] = []
        if include_auto:
            self.features.extend(self._detect_features())

    @classmethod
    def from_file(
        cls, path: str | Path, *, include_auto: bool = True,
        thresholds: Thresholds | None = None,
    ) -> DiagramAnalyzer:
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f'could not decode image: {path}')
        return cls(image, include_auto=include_auto, thresholds=thresholds)

    def _detect_features(self) -> list[FeatureSpec]:
        threshold_type = cv2.THRESH_BINARY_INV if float(self.image.mean()) >= 127 else cv2.THRESH_BINARY
        _, foreground = cv2.threshold(
            self.image, 0, 255, threshold_type | cv2.THRESH_OTSU,
        )
        count, _, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
        total_area = self.width * self.height
        components = [
            (x, y, width, height, area)
            for x, y, width, height, area in stats[1:count]
            if area >= 2 and area <= total_area * 0.3 and width > 0 and height > 0
        ]
        components.sort(key=lambda item: item[4], reverse=True)
        if len(components) > 1000:
            components = components[:1000]
        features: list[FeatureSpec] = []
        for index, (x, y, width, height, area) in enumerate(components):
            aspect = max(width, height) / min(width, height)
            category: Category = (
                'line' if aspect >= 5 else
                'symbol' if area >= max(total_area * 0.0002, 20) else 'detail'
            )
            stroke = float(min(width, height))
            extent = float(height if category == 'text' else min(width, height))
            contrast = self._local_contrast(x, y, width, height, foreground)
            features.append(FeatureSpec(
                f'auto-{index + 1}', (int(x), int(y), int(width), int(height)),
                category, stroke_px=stroke, extent_px=extent, contrast=contrast,
            ))
        return features

    def _local_contrast(
        self, x: int, y: int, width: int, height: int, foreground: np.ndarray,
    ) -> float:
        padding = max(2, min(width, height))
        left, top = max(0, x - padding), max(0, y - padding)
        right, bottom = min(self.width, x + width + padding), min(self.height, y + height + padding)
        gray_region = self.image[top:bottom, left:right]
        mask = foreground[top:bottom, left:right] != 0
        foreground_values = gray_region[mask]
        background_values = gray_region[~mask]
        if foreground_values.size == 0 or background_values.size == 0:
            return 1.0
        foreground_mean = float(foreground_values.mean())
        background_mean = float(background_values.mean())
        return min(abs(background_mean - foreground_mean) / max(background_mean, 1.0), 1.0)

    def add_feature(self, feature: FeatureSpec) -> None:
        x, y, width, height = feature.bbox
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise ValueError(f'feature {feature.name!r} is outside the image')
        if feature.category == 'text':
            left, top, right, bottom = x, y, x + width, y + height
            self.features = [
                existing for existing in self.features
                if not (
                    existing.name.startswith('auto-')
                    and existing.bbox[0] < right and existing.bbox[0] + existing.bbox[2] > left
                    and existing.bbox[1] < bottom and existing.bbox[1] + existing.bbox[3] > top
                )
            ]
        self.features.append(feature)

    def evaluate(
        self,
        width_mm: float,
        params: ViewingParameters | None = None,
        *,
        height_mm: float | None = None,
    ) -> LegibilityEvaluation:
        _positive('width_mm', width_mm)
        params = params or ViewingParameters(thresholds=self.thresholds)
        expected_height = width_mm * self.height / self.width
        if height_mm is not None:
            _positive('height_mm', height_mm)
            if not math.isclose(height_mm, expected_height, rel_tol=0.01):
                raise ValueError('height_mm must preserve the image aspect ratio')

        preset = _PRESETS[params.legibility_level] / params.visual_acuity
        mm_per_pixel = width_mm / self.width
        visibility: list[FeatureVisibility] = []
        for feature in self.features:
            _, _, box_width, box_height = feature.bbox
            stroke_px = feature.stroke_px if feature.stroke_px is not None else min(box_width, box_height)
            extent_px = feature.extent_px if feature.extent_px is not None else (
                box_height if feature.category == 'text' else min(box_width, box_height)
            )
            contrast = feature.contrast if feature.contrast is not None else 1.0
            contrast_penalty = min(
                params.max_contrast_penalty,
                params.contrast_reference / max(contrast, params.contrast_floor),
            )
            stroke_angle = _mm_to_angle(stroke_px * mm_per_pixel, params.distance_mm)
            extent_angle = _mm_to_angle(extent_px * mm_per_pixel, params.distance_mm)
            stroke_threshold = params.thresholds.stroke[feature.category] * preset
            extent_threshold = params.thresholds.extent[feature.category] * preset
            margins = [stroke_angle / stroke_threshold / contrast_penalty] if stroke_threshold else []
            if extent_threshold:
                margins.append(extent_angle / extent_threshold / contrast_penalty)
            margin = min(margins) if margins else math.inf
            visibility.append(FeatureVisibility(
                feature.name, margin, margin >= 1, feature.critical,
            ))

        if not visibility:
            visible_fraction = 0.0
            critical_visible = False
        else:
            visible_fraction = sum(item.visible for item in visibility) / len(visibility)
            critical_visible = all(item.visible for item in visibility if item.critical)
        passes = (
            bool(visibility)
            and visible_fraction >= params.required_feature_fraction
            and (critical_visible or not params.critical_features_required)
        )
        return LegibilityEvaluation(
            visible_fraction, passes, visible_fraction, critical_visible, tuple(visibility),
        )

    def minimum_size(
        self,
        params: ViewingParameters | None = None,
        *,
        max_width_mm: float | None = None,
        max_height_mm: float | None = None,
    ) -> MinimumSizeResult:
        params = params or ViewingParameters(thresholds=self.thresholds)
        if max_width_mm is not None:
            _positive('max_width_mm', max_width_mm)
        if max_height_mm is not None:
            _positive('max_height_mm', max_height_mm)
        if not self.features:
            return MinimumSizeResult(False, False, 0, 0, 0, 0, ())

        low, high = 0.0, 1.0
        for _ in range(60):
            if self.evaluate(high, params).passes:
                break
            high *= 2
        else:
            raise RuntimeError('could not find a finite legible size')
        for _ in range(60):
            middle = (low + high) / 2
            if self.evaluate(middle, params).passes:
                high = middle
            else:
                low = middle
        height = high * self.height / self.width
        evaluation = self.evaluate(high, params)
        fits = (
            (max_width_mm is None or high <= max_width_mm)
            and (max_height_mm is None or height <= max_height_mm)
        )
        intrinsic_width_mm = self.width * 25.4 / 96
        return MinimumSizeResult(
            True, fits, high, height, high / intrinsic_width_mm,
            evaluation.score,
            tuple(item.name for item in evaluation.features if not item.visible),
        )


def minimum_diagram_size(
    image: np.ndarray,
    params: ViewingParameters | None = None,
    *,
    max_width_mm: float | None = None,
    max_height_mm: float | None = None,
) -> MinimumSizeResult:
    return DiagramAnalyzer(image).minimum_size(
        params, max_width_mm=max_width_mm, max_height_mm=max_height_mm,
    )
        