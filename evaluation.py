"""Evaluate one scientific poster image against Methods and Results.

Requires Python 3.10+ and:
    pip install --upgrade openai pydantic

Set OPENAI_API_KEY in the environment.

Library usage:
    from poster_image_evaluator import evaluate_image
    evaluation = evaluate_image(image_url, general_text, methods, results)
    print(evaluation.model_dump_json(indent=2))

Command-line usage:
    python poster_image_evaluator.py input.json -o evaluation.json

The input JSON must contain `image_url`, `general_text`, `methods`, and
`results` string fields. `image_url` may be an HTTPS URL or a supported
base64 data URL (for example, data:image/png;base64,...).
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import Enum
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ConfigDict


DEFAULT_MODEL = "gpt-6.1-sol"

EVALUATION_PROMPT = """\
# Evaluate the associated image in relation to the source text.

You are an expert evaluator of scientific and technical images intended for
conference presentation posters.

You will receive:
- One image.
- General research text providing scientific, medical, educational, psychological, technical, or other relevant context.
- Materials and Methods section text.
- Results section text.

Your task is to classify and evaluate how effectively the image supports each of the two
poster sections.

## Stage 1: Image classification

    Classify the provided image into exactly one of the following categories:
    - 'table': Structured data arranged in rows and columns.
    - 'chart': Data visualizations like bar graphs, line plots, pie charts, or scatter plots.
    - 'diagram': Flowcharts, architecture schematics, mind maps, circuit diagrams, or labeled illustrations.
    - 'math expression': Mathematical equations, formulas, or symbolic expressions.
    - 'image': Default category for photographs, general artwork, screenshots, or anything that does not clearly fit the categories above.

## Stage 2: Shared image analysis

Analyze the image once to identify:
- Its type and principal subject.
- The scientific or technical concepts represented.
- Its important visual elements, labels, and relationships.
- Its relationship to the general research text.
- Any apparent inaccuracies, ambiguities, or unsupported implications.

## Stage 3: Independent section evaluations

Evaluate the image's support for each of the following two poster sections:

### Materials and Methods

Determine how effectively the image explains or documents the materials,
experimental design, procedures, system architecture, data processing, or
analytical methods described in the Materials and Methods text.

### Results

Determine how effectively the image communicates actual findings,
observations, measurements, comparisons, or outcomes described in the
Results text.

For each section, evaluate six criteria:
1. Relevance: Direct relationship to the section's subject matter.
2. Technical accuracy: Whether depicted scientific/technical information is
   correct and consistent with supplied source materials.
3. Specificity: How precisely the image represents the particular concepts,
   methods, or findings described in the section.
4. Completeness: Coverage of the specific concepts, procedures, or findings
   the image purports to represent, NOT the entire poster section.
5. Evidentiary support: Whether the image itself supplies direct visual
   information substantiating claims made in the section. A conceptual
   illustration is not experimental evidence.
6. Potential for misinterpretation: Whether visual presentation, omitted
   qualifications, or ambiguous labels could lead to mistaken conclusions.
   Assess risks clearly; do not treat the criterion as a positive score.

Use this ordinal rating scale for each section:
1. none: No meaningful support.
2. weakly: Broadly related, with little direct support.
3. somewhat: Some meaningful support, but substantial limitations.
4. significantly: Clear support for important elements, with meaningful gaps.
5. highly: Direct and accurate support for most relevant information, with only minor limitations.
6. definitively: Direct and unambiguous support for the specific information being evaluated, without material omissions or unsupported implications.

## Evaluation requirements

- Apply all six criteria separately to each section.
- Assign exactly one overall rating to Materials and Methods and one to Results.
- Do not average numerical scores to determine the overall ratings.
- Base each overall rating on the importance of the supported information and material inaccuracies or omissions.
- Treat general research text as context, not a substitute for section-specific evidence.
- Evaluate the two sections independently; do not let one section's rating determine or influence the other's rating.
- Distinguish depiction of methodology from evidence of results.
- Do not penalize an image for omitting unrelated content or content outside the particular scope the image is intended to illustrate.
- Do not assume information not visible in the image or present in the text.
- Distinguish contradictions from mere omissions.
- If a criterion cannot be assessed, explicitly state that limitation in its assessment rather than fabricating a conclusion.
- A technically accurate image can receive a low rating if it does notsupport the particular section.
- Treat 'definitively' as representational support, not independentvscientific proof.
- Be cautious about declaring an image objectively accurate when supplied materials do not independently establish its correctness.

## Output requirements

- Return the requested structured output.
- Keep each criterion assessment to one concise sentence.
- Keep each overall explanation to at most three sentences.
- Include only material supported elements, limitations, and improvements.
- Use empty arrays when no items apply.
- Avoid repeating the same observation across multiple fields.
"""


class Rating(str, Enum):
    none = "none"
    weakly = "weakly"
    somewhat = "somewhat"
    significantly = "significantly"
    highly = "highly"
    definitively = "definitively"


class Criteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevance: str
    technical_accuracy: str
    specificity: str
    completeness: str
    evidentiary_support: str
    potential_for_misinterpretation: str


class SectionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Rating
    explanation: str
    criteria: Criteria
    supported_elements: list[str]
    limitations: list[str]
    suggested_improvements: list[str]


class ImageAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    description: str
    research_context: str


class SectionEvaluations(BaseModel):
    """Fixed fields guarantee both sections are present."""

    model_config = ConfigDict(extra="forbid")

    materials_and_methods: SectionEvaluation
    results: SectionEvaluation


class EvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_analysis: ImageAnalysis
    evaluations: SectionEvaluations


def evaluate_image(
    image_url: str,
    general_text: str,
    methods: str,
    results: str,
    *,
    model: str = DEFAULT_MODEL,
    client: OpenAI | None = None,
    image_detail: str = "high",
) -> EvaluationResponse:
    """Return structured independent evaluations for both poster sections.

    `image_url` is an accessible HTTPS image URL or an image data URL.
    The caller can pass an existing OpenAI client for connection reuse.
    """
    for name, value in {
        "image_url": image_url,
        "general_text": general_text,
        "methods": methods,
        "results": results,
        "model": model,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")

    if image_detail not in ("auto", "low", "high", "original"):
        raise ValueError("image_detail must be auto, low, high, or original")

    source_data = {
        "general_text": general_text,
        "sections": {
            "materials_and_methods": methods,
            "results": results,
        },
    }

    api = client if client is not None else OpenAI()
    response = api.responses.parse(
        model=model,
        instructions=EVALUATION_PROMPT,
        text_format=EvaluationResponse,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": image_url,
                        "detail": image_detail,
                    },
                    {
                        "type": "input_text",
                        "text": json.dumps(source_data, ensure_ascii=False),
                    },
                ],
            }
        ],
    )

    if response.output_parsed is None:
        raise RuntimeError(
            "The API did not return a parsed evaluation "
            f"(response status: {response.status})."
        )

    return response.output_parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path, help="JSON input containing image_url and section text")
    parser.add_argument("-o", "--output", type=Path, help="Write resulting JSON to this file (default: stdout)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"API model (default: {DEFAULT_MODEL})")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("The input JSON must contain an object")
        evaluation = evaluate_image(
            image_url=payload["image_url"],
            general_text=payload["general_text"],
            methods=payload["methods"],
            results=payload["results"],
            model=args.model,
        )
        output = evaluation.model_dump_json(indent=2)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
