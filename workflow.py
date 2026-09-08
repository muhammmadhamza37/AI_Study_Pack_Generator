"""
workflow.py
-----------
Orchestrates the multi-stage AI Study Pack workflow.

Pipeline:
1. Planning
2. Content Generation
3. Assessment
4. Review
5. Refinement

Each stage receives the shared WorkflowContext and writes its output back to it.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable
import json
import time

from google import genai
from google.genai import types

from prompts import (
    PLANNING_PROMPT,
    CONTENT_PROMPT,
    ASSESSMENT_PROMPT,
    REVIEW_PROMPT,
    REFINEMENT_PROMPT,
)


class WorkflowError(Exception):
    """Raised when a workflow stage cannot complete successfully."""


@dataclass
class WorkflowContext:
    source_text: str
    topic: str
    level: str
    study_time: int
    question_count: int
    model_name: str

    planning: Dict[str, Any] = field(default_factory=dict)
    content: Dict[str, Any] = field(default_factory=dict)
    assessment: Dict[str, Any] = field(default_factory=dict)
    review: Dict[str, Any] = field(default_factory=dict)
    final_pack: Dict[str, Any] = field(default_factory=dict)

    stage_status: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


def _json_instruction() -> str:
    return (
        "\n\nReturn ONLY valid JSON. Do not use Markdown code fences. "
        "Do not add commentary outside the JSON object."
    )


def _safe_json(text: str) -> Dict[str, Any]:
    """Parse JSON returned by the model and tolerate accidental code fences."""
    if not text:
        raise WorkflowError("The AI returned an empty response.")

    text = text.strip()

    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Try to recover the first complete JSON object.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise WorkflowError("The AI returned invalid JSON.") from exc
        else:
            raise WorkflowError("The AI returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise WorkflowError("The AI response must be a JSON object.")

    return data


def _call_ai(
    client: genai.Client,
    prompt: str,
    model_name: str,
    retries: int = 2,
) -> Dict[str, Any]:
    """Call Gemini with retry logic and structured JSON output."""
    last_error = None

    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt + _json_instruction(),
                config=types.GenerateContentConfig(
                    temperature=0.25,
                    response_mime_type="application/json",
                    max_output_tokens=14000,
                ),
            )
            return _safe_json(response.text)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))

    raise WorkflowError(f"Gemini request failed after retries: {last_error}")


def _validate_stage(name: str, data: Dict[str, Any]) -> None:
    """Basic validation so a broken stage does not silently corrupt later stages."""
    if not isinstance(data, dict) or not data:
        raise WorkflowError(f"{name} returned no usable data.")


def run_planning(client: genai.Client, ctx: WorkflowContext) -> Dict[str, Any]:
    prompt = PLANNING_PROMPT.format(
        topic=ctx.topic or "Infer the main topic from the study material.",
        level=ctx.level,
        study_time=ctx.study_time,
        question_count=ctx.question_count,
        source_text=ctx.source_text[:120000],
    )
    result = _call_ai(client, prompt, ctx.model_name)
    _validate_stage("Planning", result)
    ctx.planning = result
    ctx.stage_status["Planning"] = "Completed"
    return result


def run_content_generation(
    client: genai.Client, ctx: WorkflowContext
) -> Dict[str, Any]:
    prompt = CONTENT_PROMPT.format(
        level=ctx.level,
        study_time=ctx.study_time,
        source_text=ctx.source_text[:120000],
        planning_json=json.dumps(ctx.planning, ensure_ascii=False),
    )
    result = _call_ai(client, prompt, ctx.model_name)
    _validate_stage("Content Generation", result)
    ctx.content = result
    ctx.stage_status["Content Generation"] = "Completed"
    return result


def run_assessment(
    client: genai.Client, ctx: WorkflowContext
) -> Dict[str, Any]:
    prompt = ASSESSMENT_PROMPT.format(
        level=ctx.level,
        question_count=ctx.question_count,
        planning_json=json.dumps(ctx.planning, ensure_ascii=False),
        content_json=json.dumps(ctx.content, ensure_ascii=False),
    )
    result = _call_ai(client, prompt, ctx.model_name)
    _validate_stage("Assessment", result)

    mcqs = result.get("mcqs", [])
    if not isinstance(mcqs, list):
        raise WorkflowError("Assessment stage returned an invalid MCQ list.")

    if len(mcqs) != ctx.question_count:
        raise WorkflowError(
            f"Assessment generated {len(mcqs)} MCQs; "
            f"{ctx.question_count} were requested."
        )

    for index, mcq in enumerate(mcqs, 1):
        options = mcq.get("options", [])
        answer = mcq.get("answer", "")
        if not isinstance(options, list) or len(options) != 4:
            raise WorkflowError(f"MCQ {index} does not contain exactly 4 options.")
        if answer not in options:
            raise WorkflowError(
                f"MCQ {index} has a correct answer that is not one of its options."
            )

    ctx.assessment = result
    ctx.stage_status["Assessment"] = "Completed"
    return result


def run_review(client: genai.Client, ctx: WorkflowContext) -> Dict[str, Any]:
    prompt = REVIEW_PROMPT.format(
        source_text=ctx.source_text[:120000],
        planning_json=json.dumps(ctx.planning, ensure_ascii=False),
        content_json=json.dumps(ctx.content, ensure_ascii=False),
        assessment_json=json.dumps(ctx.assessment, ensure_ascii=False),
    )
    result = _call_ai(client, prompt, ctx.model_name)
    _validate_stage("Review", result)
    ctx.review = result
    ctx.stage_status["Review"] = "Completed"
    return result


def run_refinement(
    client: genai.Client, ctx: WorkflowContext
) -> Dict[str, Any]:
    prompt = REFINEMENT_PROMPT.format(
        level=ctx.level,
        study_time=ctx.study_time,
        source_text=ctx.source_text[:120000],
        planning_json=json.dumps(ctx.planning, ensure_ascii=False),
        content_json=json.dumps(ctx.content, ensure_ascii=False),
        assessment_json=json.dumps(ctx.assessment, ensure_ascii=False),
        review_json=json.dumps(ctx.review, ensure_ascii=False),
    )
    result = _call_ai(client, prompt, ctx.model_name)
    _validate_stage("Refinement", result)

    final_mcqs = result.get("mcqs", [])
    if not isinstance(final_mcqs, list) or len(final_mcqs) != ctx.question_count:
        raise WorkflowError(
            "Refinement did not preserve the requested number of MCQs."
        )

    ctx.final_pack = result
    ctx.stage_status["Refinement"] = "Completed"
    return result


STAGES: List[tuple[str, Callable]] = [
    ("Planning", run_planning),
    ("Content Generation", run_content_generation),
    ("Assessment", run_assessment),
    ("Review", run_review),
    ("Refinement", run_refinement),
]


def run_workflow(api_key: str, ctx: WorkflowContext) -> WorkflowContext:
    """Run all five stages sequentially with shared context."""
    if not api_key:
        raise WorkflowError("GEMINI_API_KEY is missing.")

    client = genai.Client(api_key=api_key)

    for name, stage_function in STAGES:
        try:
            stage_function(client, ctx)
        except Exception as exc:
            ctx.stage_status[name] = "Failed"
            ctx.errors.append(f"{name}: {exc}")
            raise WorkflowError(f"{name} stage failed: {exc}") from exc

    return ctx
