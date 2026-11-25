import numpy as np
import cv2
import pytest

from legibility import (DiagramAnalyzer, FeatureSpec, ViewingParameters,
                                minimum_diagram_size)
from source_processing import (ProcessingError, _diagram_legibility_parameters,
                               image_legibility_score)


def sample():
    im = np.full((200, 300), 255, dtype=np.uint8)
    cv2.line(im, (20, 25), (260, 25), 0, 2)
    cv2.rectangle(im, (50, 60), (90, 100), 0, -1)
    return im


def test_minimum_and_repeated_evaluation():
    analyzer = DiagramAnalyzer(sample())
    result = analyzer.minimum_size()
    assert result.feasible and result.minimum_width_mm > 0
    assert result.minimum_height_mm == pytest.approx(result.minimum_width_mm * 2 / 3)
    assert analyzer.evaluate(result.minimum_width_mm).passes
    assert not analyzer.evaluate(result.minimum_width_mm * .1).passes


def test_viewing_distance_scaling():
    analyzer = DiagramAnalyzer(sample())
    one = analyzer.minimum_size(ViewingParameters(distance_mm=900)).minimum_width_mm
    two = analyzer.minimum_size(ViewingParameters(distance_mm=1800)).minimum_width_mm
    assert two == pytest.approx(2 * one, rel=1e-4)


def test_manual_critical_and_fraction():
    analyzer = DiagramAnalyzer(sample(), include_auto=False)
    analyzer.add_feature(FeatureSpec('large', (50, 60, 40, 40), 'symbol',
                                     stroke_px=20, extent_px=40, contrast=1))
    analyzer.add_feature(FeatureSpec('tiny-label', (20, 20, 20, 10), 'text',
                                     critical=True, stroke_px=1, extent_px=10, contrast=1))
    params = ViewingParameters(required_feature_fraction=.5)
    min_width = analyzer.minimum_size(params).minimum_width_mm
    assert analyzer.evaluate(min_width, params).critical_features_visible
    without = ViewingParameters(required_feature_fraction=.5,
                                critical_features_required=False)
    assert analyzer.minimum_size(without).minimum_width_mm < min_width


def test_bounds_do_not_silently_shrink():
    result = minimum_diagram_size(sample(), max_width_mm=1)
    assert not result.fits_constraints
    assert result.minimum_width_mm > 1


def test_aspect_validation_and_empty_case():
    a = DiagramAnalyzer(sample(), include_auto=False)
    assert not a.minimum_size().feasible
    with pytest.raises(ValueError):
        a.evaluate(200, height_mm=200)


def test_annotated_text_replaces_components():
    analyzer = DiagramAnalyzer(sample())
    before = len(analyzer.features)
    analyzer.add_feature(FeatureSpec('line-tag', (10, 15, 260, 20), 'text',
                                     stroke_px=2, contrast=.9))
    assert len(analyzer.features) <= before + 1
    assert any(f.name == 'line-tag' for f in analyzer.features)


def test_image_legibility_scores_raster_and_vector_rendering():
    image = sample()
    success, encoded = cv2.imencode('.png', image)
    assert success
    raster = encoded.tobytes()
    vector = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 225 150">
        <path d="M0 0" />
    </svg>'''

    pixel_score = image_legibility_score(raster, 'image/png')
    vector_score = image_legibility_score(
        vector, 'image/svg+xml', raster_data=raster,
    )

    assert 0 <= pixel_score <= 1
    assert vector_score == pytest.approx(pixel_score)


def test_vector_score_requires_a_rasterized_rendering():
    with pytest.raises(ValueError, match='raster rendering'):
        image_legibility_score(b'<svg/>', 'image/svg+xml')


def test_legibility_environment_settings_accept_inches_and_centimeters(monkeypatch):
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE', '36')
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE_UNIT', 'inches')
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_TARGET_FEATURE_ANGLE_ARCMIN', '7.5')
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_REQUIRED_FEATURE_FRACTION', '0.8')
    params, target_angle, display_dpi = _diagram_legibility_parameters()
    assert params.distance_mm == pytest.approx(914.4)
    assert params.required_feature_fraction == 0.8
    assert target_angle == 7.5
    assert display_dpi == 96

    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE', '91.44')
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE_UNIT', 'centimeters')
    params, _, _ = _diagram_legibility_parameters()
    assert params.distance_mm == pytest.approx(914.4)


def test_legibility_environment_values_are_validated(monkeypatch):
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE_UNIT', 'feet')
    with pytest.raises(ProcessingError, match='must be inches or centimeters'):
        _diagram_legibility_parameters()

    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE_UNIT', 'inches')
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_REQUIRED_FEATURE_FRACTION', '1.1')
    with pytest.raises(ProcessingError, match='must be at most 1'):
        _diagram_legibility_parameters()


def test_image_legibility_score_uses_configured_viewing_distance(monkeypatch):
    success, encoded = cv2.imencode('.png', sample())
    assert success
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE', '36')
    near_score = image_legibility_score(encoded.tobytes(), 'image/png')
    monkeypatch.setenv('DIAGRAM_LEGIBILITY_VIEWING_DISTANCE', '1000')
    far_score = image_legibility_score(encoded.tobytes(), 'image/png')
    assert near_score > far_score
