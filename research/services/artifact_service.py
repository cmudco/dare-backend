"""
Artifacts: the Presenter turns a request into one renderable artifact.

The reply is a metadata JSON line, the content marker, then the raw content, so
the model never escapes a large HTML/SVG/Markdown blob into a JSON string.
docx and pptx content is a JSON object checked with the same validators the
chat tools use.
"""

import json
import logging

import django_rq
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from dare_tools.services.pptx_tool import (
    execute_create_pptx,
    get_create_pptx_tool_openai,
)
from dare_tools.services.registry import (
    execute_create_docx,
    get_create_docx_tool_openai,
)
from research.constants import AgentRole, AgentRunStatus, ResearchSessionMode
from research.models import (
    ResearchAgentRun,
    ResearchArtifact,
    ResearchSession,
    SoulFile,
)
from research.services.json_reply import find_json_object
from research.services.profile_service import hermes_for
from research.services.prompt_builder import ARTIFACT_CONTENT_MARKER, artifact_prompt
from research.services.run_service import RunStream, complete, set_detail, start

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    "diagram",
    "html",
    "svg",
    "excalidraw",
    "code",
    "document",
    "docx",
    "pptx",
}

# Structured types: (validator, key of the validated config, tool schema).
_STRUCTURED = {
    "docx": (execute_create_docx, "doc_config", get_create_docx_tool_openai),
    "pptx": (execute_create_pptx, "ppt_config", get_create_pptx_tool_openai),
}


def _strip_code_fence(text):
    """Drop a single wrapping ``` fence if the model added one (no regex)."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    newline = text.find("\n")
    if newline != -1:
        text = text[newline + 1 :]
    end = text.rfind("```")
    if end != -1:
        text = text[:end]
    return text.strip()


def _build_artifact(atype, title, content, errors):
    """The artifact to save, or None with the reason appended to ``errors``."""
    atype = str(atype or "").strip().lower()
    if atype not in ALLOWED_TYPES:
        errors.append(f'"{atype}" is not an allowed artifact type')
        return None
    if atype in _STRUCTURED:
        validator, config_key, _ = _STRUCTURED[atype]
        try:
            config = json.loads(_strip_code_fence(content))
        except json.JSONDecodeError:
            errors.append(f"the {atype} content was not valid JSON")
            return None
        if not isinstance(config, dict):
            errors.append(f"the {atype} content must be a JSON object")
            return None
        result = validator(config)
        if not result.get("success"):
            logger.warning(
                "%s artifact failed validation: %s", atype, result.get("error")
            )
            errors.append(f"{atype}: {result.get('error')}")
            return None
        content = json.dumps(result[config_key], indent=2)
    return {
        "artifact_type": atype,
        "title": str(title or atype).strip(),
        "content": content,
    }


def parse_artifacts(output, errors=None):
    """The artifact in the Presenter's reply as ``[{artifact_type, title,
    content}]``, or ``[]``; ``errors`` collects why one was rejected."""
    if errors is None:
        errors = []
    head, marker, content = (output or "").partition(ARTIFACT_CONTENT_MARKER)
    meta = find_json_object(head, "type") if marker else None
    if meta is None or not content.strip():
        errors.append("the reply was not the artifact contract")
        return []
    built = _build_artifact(
        meta.get("type"), meta.get("title"), content.strip(), errors
    )
    return [built] if built else []


def request_artifact(project, user, prompt, artifact_type=""):
    """Create the Presenter run for ``prompt`` and queue it."""
    run = ResearchAgentRun.objects.create(
        session=ResearchSession.get_or_create_artifact_session(project, user),
        project=project,
        user=user,
        role=AgentRole.PRESENTER,
        mode=ResearchSessionMode.ARTIFACT,
        task=prompt,
        status=AgentRunStatus.RUNNING,
        status_detail="Queued…",
        soul_file_version=SoulFile.current_label(project),
        allowed_tools=["skills"],
        selected_context={"artifactType": artifact_type},
        started_at=timezone.now(),
    )
    transaction.on_commit(
        lambda: django_rq.get_queue("default").enqueue(
            "research.tasks.run_artifact_job", run.id
        )
    )
    return run


def run_artifact(run_id):
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if run is None:
        logger.warning("Artifact run %s not found", run_id)
        return
    hermes = hermes_for(run.project)
    set_detail(run, "Generating artifact…")
    artifact_type = (run.selected_context or {}).get("artifactType", "")
    schema = None
    if artifact_type in _STRUCTURED:
        schema = _STRUCTURED[artifact_type][2]()["function"]["parameters"]
    if not start(run, hermes, artifact_prompt(run, schema)):
        return
    stream = RunStream(run, hermes)
    for _ in stream:
        pass
    output = stream.result(wait_seconds=settings.HERMES_RUN_WAIT_SECONDS)
    if output is None:
        return
    artifacts = parse_artifacts(output)
    for art in artifacts:
        ResearchArtifact.objects.create(
            project=run.project,
            run=run,
            artifact_type=art["artifact_type"],
            title=art["title"][:255],
            content=art["content"],
            source="hermes",
            provenance={"runId": run.id, "retrievedAt": timezone.now().isoformat()},
        )
    complete(
        run,
        hermes,
        "Generated 1 artifact." if artifacts else "No artifact produced.",
        output,
    )
