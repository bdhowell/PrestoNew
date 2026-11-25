EVALUATION_PROMPT = """
# Evaluate the associated image in relation to the source text.

You are an expert evaluator of scientific and technical images intended for conference presentation posters.

You will receive:

* An image.
* General research text providing scientific context.
* Materials and Methods section text.
* Results section text.

Your task is to evaluate how effectively the image supports each of the two poster sections.

##  Stage 1: Shared image analysis

Analyze the image once to identify:

* Its type and principal subject.
* The scientific or technical concepts represented.
* Its important visual elements, labels, and relationships.
* Its relationship to the general research text.
* Any apparent inaccuracies, ambiguities, or unsupported implications.

## Stage 2: Independent section evaluations

Evaluate the image separately for:

### Materials and Methods

Determine how effectively the image explains or documents the materials, experimental design, procedures, system architecture, data processing, or analytical methods described in the Materials and Methods text.

### Results

Determine how effectively the image communicates the actual findings, observations, measurements, comparisons, or outcomes described in the Results text.

For each section, evaluate these six criteria:

1. Relevance
2. Technical accuracy
3. Specificity
4. Completeness
5.  Evidentiary support
6. Potential for misinterpretation

Use the following ordinal rating scale for each section:

1. none: No meaningful support.
2. weakly: Broadly related, with little direct support.
3. somewhat: Some meaningful support, but substantial limitations.
4. significantly: Clear support for important elements, with meaningful gaps.
5. highly: Direct and accurate support for most relevant information, with only minor limitations.
6. definitively: Direct and unambiguous support for the specific information being evaluated, without material omissions or unsupported implications.

# Evaluation requirements

* Apply all six criteria separately to each section.
* Assign one overall rating to Materials and Methods and one to Results.
* Do not average numerical scores to determine the ratings.
* Base each overall rating on the importance of the supported information and any material inaccuracies or omissions.
* Consider the general research text as context, not as a substitute for section-specific evidence.
* Do not allow the evaluation of one section to influence the other.
* Distinguish a depiction of methodology from evidence of results.
* Do not penalize an image for omitting unrelated content outside its intended scope.
* Do not assume information that is not visible in the image or present in the supplied text.
* Distinguish genuine contradictions from information that is merely omitted.
* If a criterion cannot be assessed from the supplied materials, explicitly state that limitation.
* A scientifically accurate image may still receive a low rating if it does not support the particular section.
* Treat "definitively" as the highest level of representational support, not independent scientific proof.
"""

RESPONSE_STRUCTURE = """
{
  "image_analysis": {
    "type": "architecture diagram",
    "description": "Description of the image",
    "research_context": "Relationship to the research"
  },
  "evaluations": {
    "materials_and_methods": {
      "rating": "highly",
      "explanation": "Detailed explanation",
      "criteria": {
        "relevance": "Assessment",
        "technical_accuracy": "Assessment",
        "specificity": "Assessment",
        "completeness": "Assessment",
        "evidentiary_support": "Assessment",
        "potential_for_misinterpretation": "Assessment"
      },
      "supported_elements": [
        "Specific method illustrated"
      ],
      "limitations": [
        "Important methodological limitation"
      ],
      "suggested_improvements": [
        "Suggested improvement"
      ]
    },
    "results": {
      "rating": "weakly",
      "explanation": "Detailed explanation",
      "criteria": {
        "relevance": "Assessment",
        "technical_accuracy": "Assessment",
        "specificity": "Assessment",
        "completeness": "Assessment",
        "evidentiary_support": "Assessment",
        "potential_for_misinterpretation": "Assessment"
      },
      "supported_elements": [],
      "limitations": [
        "No experimental outcomes depicted"
      ],
      "suggested_improvements": [
        "Add quantitative findings"
      ]
    }
  }
}
"""

response = client.responses.create(
    model="gpt-6",
    instructions=EVALUATION_PROMPT,
    response_format=RESPONSE_STRUCTURE,
    input=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": "high",
                },
                {
                    "type": "input_text",
                    "text": json.dumps(methods),
                    "section": "Materials and Methods",
                },
                {
                    "type": "input_text",
                    "text": json.dumps(results),
                    "section": "Results",
                }
              ]
        }
    ],
)

