"""Extract document text and visual elements with Docling for IBM watsonx.

Requires Python 3.10+ and: pip install 'docling==2.133.0'
Uses DoclingServiceClient, the official async-job-aware service client. Supply
DOCLING_SERVICE_URL and DOCLING_SERVICE_API_KEY from your Docling service
(not the watsonx foundation-model endpoint or an IAM bearer token).

Example:
    from docling_watsonx_extractor import extract_document
    result = extract_document('paper.pdf', output_dir='extracted')
    for item in result['elements']:
        print(item['id'], item['kind'], item['asset'], item['caption_refs'])

Inputs: local paths, bytes, or binary streams. Bytes/streams require filename.
Outputs: JSON-compatible dict, extraction.json, full Docling document JSON,
text with inline element markers, and PNG picture/table/formula crops where
available. All output asset paths are relative to output_dir. Each call creates
a separate run directory. Element IDs are Docling JSON pointers scoped to that
run. The full document retains original text, hierarchy, provenance, annotations,
chart data and native references.

iWork: export to PDF using installed Pages/Numbers/Keynote on macOS by default.
The applications must have Automation permission. Else supply pdf_exporter:
    def exporter(source: Path, destination: Path) -> Path: ...
The callback must return a real PDF. Supply it with render_office=True to render
DOCX/PPTX/XLSX too: recommended when vector charts/figures and visual math matter.
Native Office conversion preserves structure but may omit rendered drawings.
Use native_iwork=True only if your service supports your particular iWork input;
Pages and Keynote support depends on service version; Numbers needs PDF export.

Extraction is best effort. A rendered chart is a picture; chart data remains in
annotations if Docling recognizes it. Formula text/LaTeX is model output, not a
guarantee of faithful symbolic reconstruction. Explicit Docling links are kept;
numbered textual mentions are separate, marked heuristic, and can be ambiguous.
No nearby-paragraph association or missing chart values are invented. PDF export
can change pagination and sheet layout; provenance then refers to that PDF.
No source document or API key is saved by this module.

CLI: python docling_watsonx_extractor.py input.pdf --output extracted
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

SUPPORTED = {'.pdf', '.docx', '.pptx', '.xlsx', '.pages', '.numbers', '.key'}
IWORK = {'.pages': 'Pages', '.numbers': 'Numbers', '.key': 'Keynote'}
OFFICE = {'.docx', '.pptx', '.xlsx'}
# Single numbered references; ranges/lists and bare parenthetical equation
# numbers deliberately aren't inferred.
MENTION = re.compile(
    r'\b(?P<label>fig(?:ure)?s?\.?|charts?|graphs?|tables?|eq(?:uation)?s?\.?)'
    r'\s*\(?\s*(?P<number>\d+(?:\.\d+)*[A-Za-z]?)\s*\)?', re.I
)


class ExtractionError(RuntimeError):
    """Conversion, export, or output failure."""


def export_iwork_pdf(source: Path, destination: Path, timeout: float = 180) -> Path:
    """Export through Apple's installed application; never modify the source."""
    app = IWORK.get(source.suffix.lower())
    if not app or sys.platform != 'darwin':
        raise ExtractionError('iWork PDF export requires macOS and the matching '
                              'Apple app, or supply a pdf_exporter callback.')
    # Paths are passed as argv, never interpolated into AppleScript source.
    script = '''on run argv
        set inputFile to POSIX file (item 1 of argv)
        set outputFile to POSIX file (item 2 of argv)
        tell application "APP_NAME"
            set openedDocument to open inputFile
            try
                export openedDocument to outputFile as PDF
            on error errorMessage number errorNumber
                close openedDocument saving no
                error errorMessage number errorNumber
            end try
            close openedDocument saving no
        end tell
    end run'''.replace('APP_NAME', app)
    try:
        subprocess.run(['osascript', '-e', script, str(source), str(destination)],
                       check=True, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExtractionError(f'{app} PDF export failed; check the app and '
                              'macOS Automation permission.') from exc
    return destination


def _value(value: Any) -> str:
    return str(getattr(value, 'value', value))


def _json(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json', by_alias=True, exclude_none=True)
    return value


def _refs(item: Any, field: str) -> list[str]:
    return [ref.cref for ref in getattr(item, field, [])]


def _reference_key(match: re.Match) -> tuple[str, str]:
    label = match['label'].lower()
    family = ('table' if label.startswith('tab') else
              'equation' if label.startswith('eq') else 'figure')
    return family, match['number'].lower()


def _build_manifest(document: Any, directory: Path) -> dict[str, Any]:
    from docling_core.types.doc import PictureItem, TableItem, TextItem

    elements: list[dict[str, Any]] = []
    source_texts: list[dict[str, Any]] = []
    order: list[dict[str, Any]] = []
    warnings: list[str] = []
    asset_dir = directory / 'assets'
    asset_dir.mkdir()
    # include captions and text nested inside figures, not just top-level text
    for item, level in document.iterate_items(traverse_pictures=True):
        label = _value(getattr(item, 'label', ''))
        is_formula = label == 'formula'
        is_element = isinstance(item, (PictureItem, TableItem)) or is_formula
        record = {
            'id': item.self_ref, 'label': label, 'level': level,
            'parent_ref': getattr(getattr(item, 'parent', None), 'cref', None),
            'provenance': [_json(p) for p in getattr(item, 'prov', [])],
            'source': _json(getattr(item, 'source', None)),
            'metadata': _json(getattr(item, 'meta', None)),
        }
        if is_element:
            kind = ('picture' if isinstance(item, PictureItem) else
                    'table' if isinstance(item, TableItem) else 'formula')
            record.update(kind=kind, asset=None, caption_refs=_refs(item, 'captions'),
                          reference_refs=_refs(item, 'references'),
                          footnote_refs=_refs(item, 'footnotes'),
                          annotations=[_json(a) for a in getattr(item, 'annotations', [])],
                          text=getattr(item, 'text', None))
            if isinstance(item, TableItem):
                record['data'] = _json(item.data)
            images = []
            # Preserve crops for each provenance region, e.g. multipage tables.
            for prov_index in range(max(1, len(getattr(item, 'prov', [])))):
                try:
                    image = item.get_image(document, prov_index=prov_index)
                    if image is not None:
                        path = asset_dir / f'{kind}-{len(elements)+1:04d}-{prov_index+1}.png'
                        image.save(path, format='PNG')
                        images.append(path.relative_to(directory).as_posix())
                except Exception as exc:
                    warnings.append(f'Image unavailable for {item.self_ref} '
                                    f'region {prov_index}: {type(exc).__name__}')
            record['assets'] = images
            record['asset'] = images[0] if images else None
            if not images:
                warnings.append(f'No image crop available for {item.self_ref}.')
            elements.append(record)
        if isinstance(item, TextItem):
            text_record = {**record, 'text': item.text, 'original_text': item.orig,
                           'mentions': [], 'element_refs': []}
            source_texts.append(text_record)
        order.append({'id': item.self_ref, 'label': label,
                      'text': getattr(item, 'text', None), 'is_element': is_element})

    # Explicit structural links also run in reverse from captions/references.
    reverse: dict[str, list[str]] = {}
    for element in elements:
        for field in ('caption_refs', 'reference_refs', 'footnote_refs'):
            for ref in element[field]:
                reverse.setdefault(ref, []).append(element['id'])
    text_by_id = {text['id']: text for text in source_texts}
    targets: dict[tuple[str, str], list[str]] = {}
    for element in elements:
        captions = [text_by_id[r]['text'] for r in element['caption_refs'] if r in text_by_id]
        if element['kind'] == 'formula' and element['text']:
            captions.append(element['text'])
        for caption in captions:
            for match in MENTION.finditer(caption):
                # Only labels at the start of a caption can name this element.
                if caption[:match.start()].strip():
                    continue
                key = _reference_key(match)
                if ((key[0] == 'table' and element['kind'] == 'table') or
                    (key[0] == 'equation' and element['kind'] == 'formula') or
                    (key[0] == 'figure' and element['kind'] == 'picture')):
                    values = targets.setdefault(key, [])
                    if element['id'] not in values:
                        values.append(element['id'])
    for text in source_texts:
        text['element_refs'] = list(dict.fromkeys(reverse.get(text['id'], [])))
        for match in MENTION.finditer(text['text']):
            candidates = targets.get(_reference_key(match), [])
            text['mentions'].append({
                'literal': match.group(), 'span': [match.start(), match.end()],
                'candidate_element_refs': candidates,
                'element_ref': candidates[0] if len(candidates) == 1 else None,
                'method': 'numbered_label_heuristic',
                'resolution': 'unique' if len(candidates) == 1 else
                              'ambiguous' if candidates else 'unresolved',
            })
    lines = []
    for node in order:
        if node['is_element']:
            lines.append(f"[ELEMENT {node['id']} label={node['label']}]")
        if node['text']:
            lines.append(node['text'])
    return {'elements': elements, 'source_texts': source_texts,
            'reading_order': order, 'linked_text': '\n\n'.join(lines),
            'warnings': warnings}


def extract_document(
    source: str | Path | bytes | BinaryIO,
    *,
    output_dir: str | Path,
    filename: str | None = None,
    service_url: str | None = None,
    api_key: str | None = None,
    pdf_exporter: Callable[[Path, Path], Path] | None = None,
    render_office: bool = False,
    native_iwork: bool = False,
    describe_pictures: bool = True,
    extract_chart_data: bool = True,
    enrich_formulas: bool = True,
    allow_partial: bool = False,
    timeout: float = 600,
    service_options: dict[str, Any] | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Upload one document and return text, elements, assets and references.

    A supplied client implements convert(source=Path, options=..., raises_on_error
    =False). It remains open and is useful for connection reuse and testing.
    Otherwise this function owns/closes an official DoclingServiceClient.
    service_options can select model presets and override enrichment defaults;
    JSON output and embedded images remain required. Set allow_partial explicitly
    to retain incomplete conversions. Failures raise ExtractionError; no successful
    manifest is written for a failed conversion.
    """
    from docling.datamodel.service.options import ConvertDocumentsOptions
    from docling.service_client import DoclingServiceClient, StatusWatcherKind
    from docling_core.types.doc import ImageRefMode

    if timeout <= 0:
        raise ValueError('timeout must be positive')
    if isinstance(source, (str, Path)):
        original = Path(source).expanduser().resolve()
        source_name = original.name
        if not original.exists():
            raise FileNotFoundError(original)
        if original.is_dir() and original.suffix.lower() not in IWORK:
            raise ValueError('Only iWork package directories are accepted')
    else:
        if not filename:
            raise ValueError('filename is required for bytes or binary streams')
        source_name = Path(filename).name
        original = None
    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f'Unsupported extension {suffix!r}; expected {sorted(SUPPORTED)}')
    service_url = service_url or os.environ.get('DOCLING_SERVICE_URL')
    api_key = api_key if api_key is not None else os.environ.get('DOCLING_SERVICE_API_KEY', '')
    if client is None and not service_url:
        raise ValueError('Supply service_url or set DOCLING_SERVICE_URL')
    root = Path(output_dir).expanduser().resolve()
    run_dir = root / f'extraction-{uuid4().hex}'
    run_dir.mkdir(parents=True)
    opts = {'do_ocr': True, 'do_table_structure': True, 'include_images': True,
            'include_page_images': True, 'images_scale': 2.0,
            'do_formula_enrichment': enrich_formulas,
            'do_picture_classification': True,
            'do_picture_description': describe_pictures,
            'do_chart_extraction': extract_chart_data}
    opts.update(service_options or {})
    opts.update(to_formats=['json'], image_export_mode='embedded', include_images=True)
    options = ConvertDocumentsOptions(**opts)
    warnings = []
    with tempfile.TemporaryDirectory(prefix='docling-input-') as temporary:
        temp = Path(temporary)
        if original is None:
            original = temp / ('input' + suffix)
            content = source if isinstance(source, bytes) else source.read()
            if not isinstance(content, bytes):
                raise TypeError('source must be bytes or a binary stream')
            original.write_bytes(content)
        prepared = original
        needs_pdf = (suffix in IWORK and (not native_iwork or suffix == '.numbers')) or (
            suffix in OFFICE and render_office)
        if needs_pdf:
            exporter = pdf_exporter or (export_iwork_pdf if suffix in IWORK else None)
            if exporter is None:
                raise ValueError('render_office=True requires a pdf_exporter callback')
            prepared = Path(exporter(original, temp / 'export.pdf')).resolve()
            if not prepared.is_file() or prepared.suffix.lower() != '.pdf':
                raise ExtractionError('pdf_exporter must return an existing PDF path')
            with prepared.open('rb') as file:
                if not file.read(1024).lstrip().startswith(b'%PDF-'):
                    raise ExtractionError('pdf_exporter output is not a PDF')
            warnings.append('Provenance refers to the exported PDF; original '
                            'pagination/sheet layout may differ.')
        elif suffix in OFFICE:
            warnings.append('Native Office conversion may omit vector drawings, '
                            'charts or visual math. For visual coverage use '
                            'render_office=True with a PDF exporter.')
        if prepared.is_dir():
            raise ExtractionError('Native upload requires a single iWork file; '
                                  'export this package directory to PDF instead.')
        digest = hashlib.sha256()
        with prepared.open('rb') as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                digest.update(chunk)
        owned_client = client is None
        if owned_client:
            client = DoclingServiceClient(url=service_url, api_key=api_key,
                                           job_timeout=timeout, status_watcher=StatusWatcherKind.POLLING)
        try:
            conversion = client.convert(source=prepared, options=options, raises_on_error=False)
        finally:
            if owned_client:
                client.close()
        status = _value(conversion.status)
        if status != 'success' and not (allow_partial and status == 'partial_success'):
            raise ExtractionError(f'Docling conversion status: {status}; '
                                  f'errors: {conversion.errors}')
        document = conversion.document
        manifest = _build_manifest(document, run_dir)
        manifest.update(schema_version='1.0', source_filename=source_name,
                        processed_format=prepared.suffix.lower().lstrip('.'),
                        processed_sha256=digest.hexdigest(), status=status,
                        errors=[_json(e) for e in conversion.errors],
                        output_directory=str(run_dir),
                        docling_document='document.json', text_file='source_text.txt')
        manifest['warnings'] = warnings + manifest['warnings']
        document.save_as_json(run_dir / 'document.json', image_mode=ImageRefMode.EMBEDDED)
        (run_dir / 'source_text.txt').write_text(manifest['linked_text'], encoding='utf-8')
        (run_dir / 'extraction.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--service-url')
    parser.add_argument('--native-iwork', action='store_true')
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    result = extract_document(args.source, output_dir=args.output,
                              service_url=args.service_url,
                              native_iwork=args.native_iwork,
                              allow_partial=args.allow_partial)
    print(Path(result['output_directory']) / 'extraction.json')
