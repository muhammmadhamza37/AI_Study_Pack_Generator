"""
app.py
------
Main Streamlit application for the AI Study Pack Generator.

The UI is intentionally kept separate from workflow.py and prompts.py:
- app.py = presentation/input/output
- workflow.py = orchestration, context passing, validation, retries
- prompts.py = AI instructions
"""

import io
import json
import os
from typing import Any, Dict

import streamlit as st
from docx import Document
from pypdf import PdfReader

from workflow import WorkflowContext, WorkflowError, run_workflow


APP_TITLE = "AI Study Pack Generator"
DEFAULT_MODEL = "gemini-2.5-flash"

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📚",
    layout="wide",
)


def get_api_key() -> str:
    """Read the Gemini key from Streamlit Secrets or environment."""
    try:
        key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        key = ""
    return key or os.getenv("GEMINI_API_KEY", "")


def extract_text(uploaded_file) -> str:
    """Extract text from PDF, DOCX, or TXT."""
    name = uploaded_file.name.lower()
    data = uploaded_file.getvalue()

    if name.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")

    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if name.endswith(".docx"):
        document = Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)

    raise ValueError("Unsupported file. Use PDF, DOCX, or TXT.")


def export_markdown(pack: Dict[str, Any]) -> str:
    """Convert the final JSON study pack into a downloadable Markdown file."""
    lines = [
        f"# {pack.get('title', 'AI Study Pack')}",
        "",
        "## Overview",
        pack.get("overview", ""),
        "",
        "## Learning Objectives",
    ]
    lines += [f"- {x}" for x in pack.get("learning_objectives", [])]

    lines += ["", "## Key Concepts"]
    for item in pack.get("key_concepts", []):
        lines += [
            f"### {item.get('concept', '')}",
            item.get("explanation", ""),
            f"**Example:** {item.get('example', '')}",
            "",
        ]

    lines += ["## Summary", pack.get("summary", ""), "", "## Important Points"]
    lines += [f"- {x}" for x in pack.get("important_points", [])]

    lines += ["", "## Memory Aids"]
    lines += [f"- {x}" for x in pack.get("memory_aids", [])]

    lines += ["", "## Flashcards"]
    for i, card in enumerate(pack.get("flashcards", []), 1):
        lines += [
            f"### Card {i}",
            f"**Question:** {card.get('question', '')}",
            f"**Answer:** {card.get('answer', '')}",
            "",
        ]

    lines += ["## MCQs"]
    for i, q in enumerate(pack.get("mcqs", []), 1):
        lines.append(f"### {i}. {q.get('question', '')}")
        lines += [f"- {x}" for x in q.get("options", [])]
        lines += [
            f"**Answer:** {q.get('answer', '')}",
            f"**Explanation:** {q.get('explanation', '')}",
            f"**Difficulty:** {q.get('difficulty', '')}",
            "",
        ]

    lines += ["## Short Questions"]
    for i, q in enumerate(pack.get("short_questions", []), 1):
        lines.append(f"### {i}. {q.get('question', '')}")
        lines += [f"- {x}" for x in q.get("answer_points", [])]
        lines.append("")

    lines += ["## Study Plan"]
    for item in pack.get("study_plan", []):
        lines.append(
            f"- **{item.get('minutes', '')} min:** "
            f"{item.get('activity', '')} — {item.get('goal', '')}"
        )

    lines += ["", "## Exam Tips"]
    lines += [f"- {x}" for x in pack.get("exam_tips", [])]

    gaps = pack.get("source_gaps", [])
    if gaps:
        lines += ["", "## Source Gaps"]
        lines += [f"- {x}" for x in gaps]

    refinement = pack.get("refinement_summary", [])
    if refinement:
        lines += ["", "## Refinement Summary"]
        lines += [f"- {x}" for x in refinement]

    return "\n".join(lines)


def show_stage_status(ctx: WorkflowContext | None) -> None:
    """Display workflow progress in a compact way."""
    stages = ["Planning", "Content Generation", "Assessment", "Review", "Refinement"]

    cols = st.columns(len(stages))
    for col, stage in zip(cols, stages):
        status = ctx.stage_status.get(stage, "Pending") if ctx else "Pending"
        icon = "✅" if status == "Completed" else "❌" if status == "Failed" else "⏳"
        col.metric(stage, f"{icon} {status}")


st.title("📚 AI Study Pack Generator")
st.write(
    "A multi-stage AI workflow that plans, generates, assesses, reviews, "
    "and refines personalized study material."
)

with st.sidebar:
    st.header("⚙️ Study Settings")

    level = st.selectbox(
        "Student level",
        ["Beginner", "Intermediate", "Advanced"],
        index=1,
    )

    study_time = st.slider(
        "Available study time (minutes)",
        min_value=15,
        max_value=240,
        value=60,
        step=15,
    )

    question_count = st.slider(
        "Number of MCQs",
        min_value=3,
        max_value=20,
        value=10,
    )

    model_name = st.text_input(
        "Gemini model",
        value=DEFAULT_MODEL,
        help="Use a model available to your Gemini API project.",
    )

    st.divider()
    st.markdown(
        "**Workflow:** Planning → Content → Assessment → Review → Refinement"
    )
    st.caption("Keep GEMINI_API_KEY in Streamlit Secrets, never in GitHub.")

st.subheader("1. Provide your study material")

uploaded_file = st.file_uploader(
    "Upload PDF, DOCX, or TXT",
    type=["pdf", "docx", "txt"],
)

topic = st.text_input(
    "Topic (optional)",
    placeholder="e.g. Machine Learning — Linear Regression",
)

pasted_text = st.text_area(
    "Or paste your notes",
    height=220,
    placeholder="Paste lecture notes, textbook content, or study material...",
)

generate = st.button(
    "🚀 Run AI Study Workflow",
    type="primary",
    use_container_width=True,
)

if "workflow_context" not in st.session_state:
    st.session_state.workflow_context = None

if generate:
    source_text = ""

    if uploaded_file is not None:
        try:
            source_text = extract_text(uploaded_file)
        except Exception as exc:
            st.error(f"File extraction failed: {exc}")
            st.stop()

    if pasted_text.strip():
        source_text = (
            f"{source_text}\n\n{pasted_text.strip()}"
            if source_text
            else pasted_text.strip()
        )

    if len(source_text.strip()) < 50:
        st.error("Please provide at least 50 characters of study material.")
        st.stop()

    api_key = get_api_key()
    if not api_key:
        st.error(
            "GEMINI_API_KEY is missing. Add it to Streamlit Secrets "
            "or your environment variables."
        )
        st.stop()

    ctx = WorkflowContext(
        source_text=source_text,
        topic=topic.strip(),
        level=level,
        study_time=study_time,
        question_count=question_count,
        model_name=model_name.strip() or DEFAULT_MODEL,
    )

    progress = st.progress(0)
    status_text = st.empty()

    # The workflow itself is sequential. The UI updates after each completed stage
    # are represented in the final context and displayed after execution.
    status_text.info(
        "Running: Planning → Content Generation → Assessment → Review → Refinement"
    )

    try:
        completed = {"count": 0}

        # Streamlit cannot yield from the orchestration function without complicating
        # the simple deployment architecture, so run the complete workflow here.
        ctx = run_workflow(api_key, ctx)
        progress.progress(100)
        status_text.success("All five AI stages completed successfully.")
        st.session_state.workflow_context = ctx

    except WorkflowError as exc:
        progress.progress(
            int(
                100
                * sum(
                    1 for value in ctx.stage_status.values() if value == "Completed"
                )
                / 5
            )
        )
        st.session_state.workflow_context = ctx
        status_text.error("Workflow stopped because a stage failed.")
        st.error(str(exc))

ctx = st.session_state.get("workflow_context")

if ctx:
    st.divider()
    st.subheader("2. Workflow Status")
    show_stage_status(ctx)

    if ctx.errors:
        with st.expander("⚠️ Workflow Errors"):
            for error in ctx.errors:
                st.error(error)

    if ctx.final_pack:
        st.divider()
        st.subheader("3. Final Personalized Study Pack")

        pack = ctx.final_pack

        tabs = st.tabs(
            [
                "📌 Overview",
                "🧠 Concepts",
                "🃏 Flashcards",
                "❓ Assessment",
                "⏱️ Study Plan",
                "🎯 Review",
            ]
        )

        with tabs[0]:
            st.markdown(f"### {pack.get('title', 'Study Pack')}")
            st.write(pack.get("overview", ""))

            st.markdown("#### Learning Objectives")
            for item in pack.get("learning_objectives", []):
                st.markdown(f"- {item}")

            st.markdown("#### Important Points")
            for item in pack.get("important_points", []):
                st.markdown(f"- {item}")

            st.markdown("#### Summary")
            st.write(pack.get("summary", ""))

            st.markdown("#### Memory Aids")
            for item in pack.get("memory_aids", []):
                st.markdown(f"- {item}")

        with tabs[1]:
            for item in pack.get("key_concepts", []):
                with st.expander(item.get("concept", "Concept")):
                    st.write(item.get("explanation", ""))
                    if item.get("example"):
                        st.markdown(f"**Example:** {item['example']}")

        with tabs[2]:
            for i, card in enumerate(pack.get("flashcards", []), 1):
                with st.expander(
                    f"Card {i}: {card.get('question', 'Question')}"
                ):
                    st.write(card.get("answer", ""))

        with tabs[3]:
            st.markdown("### Multiple-Choice Questions")

            for i, q in enumerate(pack.get("mcqs", []), 1):
                st.markdown(f"**{i}. {q.get('question', '')}**")

                options = q.get("options", [])
                selected = st.radio(
                    "Select an answer",
                    options,
                    index=None,
                    key=f"mcq_{i}",
                    label_visibility="collapsed",
                )

                if selected:
                    if selected == q.get("answer"):
                        st.success("Correct!")
                    else:
                        st.error(f"Correct answer: {q.get('answer')}")
                    st.caption(q.get("explanation", ""))

            st.markdown("### Short Questions")
            for i, q in enumerate(pack.get("short_questions", []), 1):
                with st.expander(f"{i}. {q.get('question', '')}"):
                    for point in q.get("answer_points", []):
                        st.markdown(f"- {point}")

        with tabs[4]:
            for item in pack.get("study_plan", []):
                st.markdown(
                    f"**{item.get('minutes', '')} minutes — "
                    f"{item.get('activity', '')}**  \n"
                    f"{item.get('goal', '')}"
                )

            st.markdown("### Exam Tips")
            for tip in pack.get("exam_tips", []):
                st.markdown(f"- {tip}")

        with tabs[5]:
            review = ctx.review

            if review:
                cols = st.columns(5)
                scores = [
                    ("Overall", review.get("overall_score", 0)),
                    ("Grounding", review.get("grounding_score", 0)),
                    ("Coverage", review.get("coverage_score", 0)),
                    ("Clarity", review.get("clarity_score", 0)),
                    ("Assessment", review.get("assessment_score", 0)),
                ]

                for col, (label, score) in zip(cols, scores):
                    col.metric(label, f"{score}/100")

                st.markdown("#### Strengths")
                for item in review.get("strengths", []):
                    st.markdown(f"- {item}")

                st.markdown("#### Issues Found")
                for issue in review.get("issues", []):
                    st.warning(
                        f"**{issue.get('severity', '').upper()} — "
                        f"{issue.get('stage', '')}:** "
                        f"{issue.get('problem', '')}"
                    )
                    st.caption(f"Fix: {issue.get('fix', '')}")

                st.markdown("#### Refinement Summary")
                for item in pack.get("refinement_summary", []):
                    st.markdown(f"- {item}")

        st.download_button(
            "⬇️ Download Final Study Pack",
            data=export_markdown(pack),
            file_name="ai_study_pack.md",
            mime="text/markdown",
            use_container_width=True,
        )

        with st.expander("🔍 Inspect AI Workflow Context"):
            st.markdown("### Planning Output")
            st.json(ctx.planning)

            st.markdown("### Content Output")
            st.json(ctx.content)

            st.markdown("### Assessment Output")
            st.json(ctx.assessment)

            st.markdown("### Review Output")
            st.json(ctx.review)

            st.markdown("### Final Refined Output")
            st.json(ctx.final_pack)
