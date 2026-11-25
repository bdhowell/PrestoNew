import json
import os
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'legibility.py'


@pytest.fixture
def image_path(tmp_path):
    image = np.full((100, 200), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (40, 40), 0, -1)
    path = tmp_path / 'diagram.png'
    assert cv2.imwrite(str(path), image)
    return path


def run_cli(*args, environment=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith('LEGIBILITY_')}
    env.update(environment or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, env=env, check=False,
    )


def test_minimum_size_and_environment_precedence(image_path):
    baseline = run_cli(image_path)
    configured = run_cli(environment={
        'LEGIBILITY_IMAGE': str(image_path),
        'LEGIBILITY_DISTANCE_MM': '1828.8',
        'LEGIBILITY_MAX_WIDTH_MM': '1',
    })
    overridden = run_cli(image_path, '--distance-mm', '914.4', '--max-width-mm', '10000',
                         environment={'LEGIBILITY_DISTANCE_MM': 'invalid',
                                      'LEGIBILITY_MAX_WIDTH_MM': '1'})
    assert baseline.returncode == configured.returncode == overridden.returncode == 0
    default = json.loads(baseline.stdout)
    result = json.loads(configured.stdout)
    assert result['minimum_width_mm'] == pytest.approx(2 * default['minimum_width_mm'])
    assert not result['fits_constraints']
    assert json.loads(overridden.stdout)['minimum_width_mm'] == pytest.approx(default['minimum_width_mm'])
    assert json.loads(overridden.stdout)['fits_constraints']


def test_evaluate_manual_features_and_thresholds(image_path, tmp_path):
    features = tmp_path / 'features.json'
    features.write_text(json.dumps([
        {'name': 'label', 'bbox': [20, 20, 20, 20], 'category': 'text', 'critical': True}
    ]), encoding='utf-8')
    result = run_cli('--width-mm', '100', '--height-mm', '50',
                     '--text-stroke-arcmin', '0', environment={
        'LEGIBILITY_IMAGE': str(image_path),
        'LEGIBILITY_FEATURES': str(features),
        'LEGIBILITY_INCLUDE_AUTO': 'false',
        'LEGIBILITY_TEXT_STROKE_ARCMIN': '10',
        'LEGIBILITY_TEXT_EXTENT_ARCMIN': '0',
    })
    assert result.returncode == 0, result.stderr
    evaluation = json.loads(result.stdout)
    assert evaluation['passes']
    assert evaluation['features'] == [{
        'name': 'label', 'visibility_margin': None, 'visible': True, 'critical': True,
    }]


def test_empty_analysis_is_reported_as_infeasible(image_path):
    result = run_cli(image_path, '--include-auto', 'false')
    assert result.returncode == 0
    assert not json.loads(result.stdout)['feasible']


@pytest.mark.parametrize(('arguments', 'environment', 'message'), [
    (['--distance-mm', '-1'], {}, 'distance_mm must be finite and positive'),
    ([], {'LEGIBILITY_LEVEL': 'bad'}, 'unknown legibility level'),
    ([], {'LEGIBILITY_INCLUDE_AUTO': 'maybe'}, 'must be true or false'),
    (['--width-mm', '100', '--max-width-mm', '200'], {}, 'apply only to minimum-size'),
    (['--height-mm', '50'], {}, 'requires --width-mm'),
    (['--width-mm', '100', '--height-mm', '100'], {}, 'preserve the image aspect ratio'),
])
def test_invalid_configuration_reports_an_error(image_path, arguments, environment, message):
    result = run_cli(image_path, *arguments, environment=environment)
    assert result.returncode != 0
    assert message in result.stderr
    assert not result.stdout


def test_bad_feature_file_reports_an_error(image_path, tmp_path):
    features = tmp_path / 'features.json'
    features.write_text('[{"name": "bad", "bbox": [0, 0, 1, 1], "critical": "false"}]',
                        encoding='utf-8')
    result = run_cli(image_path, '--features', features)
    assert result.returncode != 0
    assert 'feature critical must be a boolean' in result.stderr
