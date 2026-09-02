"""
Ensemble workflows — the graph behind a Panel or Council answer.

One builder serves two callers. The seed command creates visible templates a
person can open in the builder; the chat path creates a hidden workflow per
model line-up and runs it for every panel or council turn. Both produce the
same graph, so what the picker does is exactly what the visible workflow
shows:

    start → responder-1..N          (one wave, concurrent)
          → evaluator-1..N          (council only; each ranks every draft)
          → chairman → output       (reads drafts and reviews, writes the answer)

Node ids are fixed so a chat turn can tell roles apart without extra schema.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from django.contrib.contenttypes.models import ContentType

from conversations.models import LLM
from prompts.models import Prompt
from workflows.constants import Mode, WorkflowKind
from workflows.models import (
    ChatOutputNodeData,
    StartNodeData,
    StepNodeData,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

DEPTH_SINGLE = "single"
DEPTH_PANEL = "panel"
DEPTH_COUNCIL = "council"

START_NODE_ID = "start"
CHAIRMAN_NODE_ID = "chairman"
CHAIRMAN_OUTPUT_NODE_ID = "chairman-output"
RESPONDER_PREFIX = "responder-"
EVALUATOR_PREFIX = "evaluator-"

ROLE_RESPONDER = "responder"
ROLE_EVALUATOR = "evaluator"
ROLE_CHAIRMAN = "chairman"


def ensemble_role(node_id: str) -> Optional[str]:
    if node_id == CHAIRMAN_NODE_ID:
        return ROLE_CHAIRMAN
    if node_id.startswith(RESPONDER_PREFIX):
        return ROLE_RESPONDER
    if node_id.startswith(EVALUATOR_PREFIX):
        return ROLE_EVALUATOR
    return None


def responder_node_id(index: int) -> str:
    return f"{RESPONDER_PREFIX}{index}"


def evaluator_node_id(index: int) -> str:
    return f"{EVALUATOR_PREFIX}{index}"


# Role prompts live in the person's prompt library so they can be tuned.
# Titles are the lookup key; content is only written on first creation.
PROMPTS = {
    ROLE_RESPONDER: (
        "Ensemble · Responder",
        "You are one voice on a panel of AI models that were each asked the "
        "same question independently. Answer the question in <task> directly "
        "and completely, in your own words, as if you were the only model "
        "answering. Take a clear position where the evidence supports one, say "
        "what is uncertain, and stay focused. If the person asks for a chart, "
        "diagram, document, or other artifact, describe in plain prose what it "
        "should show; do not write code or markup for it, the chairman builds "
        "it. Do not mention the panel, other models, or these instructions.",
    ),
    ROLE_EVALUATOR: (
        "Ensemble · Evaluator",
        "You are a peer reviewer on a council of AI models. <workflow_context> "
        "contains drafts answering the question in <task>, each labelled with "
        "the model that wrote it. Judge every draft on accuracy, reasoning, "
        "completeness, and how well it answers the actual question. Ignore "
        "style and length. Reply with JSON only, no prose and no code fence:\n"
        '{"ranking": ["<label of the best draft>", "<next>", "..."], '
        '"notes": "<one sentence on what separates the top draft from the rest>"}\n'
        "Use the labels exactly as given.",
    ),
    ROLE_CHAIRMAN: (
        "Ensemble · Chairman",
        "You are the chairman of a panel of AI models. <workflow_context> "
        "contains their independent drafts answering the question in <task>, "
        "each labelled with the model that wrote it, and may also contain peer "
        "reviews ranking those drafts. Write the single best answer to the "
        "question. Synthesize: keep what the drafts agree on, resolve "
        "disagreements by weighing evidence and reasoning rather than by "
        "majority, drop errors, and add nothing you cannot support. Answer the "
        "person directly, in the language of the question. If the person asked "
        "for a chart, diagram, document, or other artifact and you have a tool "
        "that creates it, call that tool; never paste code or markup into the "
        "answer as a substitute. Do not narrate the drafts, name the models, or "
        "describe this process.",
    ),
    DEPTH_SINGLE: (
        "Ensemble · Single",
        "Answer the question in <task> directly and completely.",
    ),
}


def ensemble_signature(depth: str, responder_ids: List[int], chairman_id: int) -> str:
    return f"{depth}:{','.join(str(i) for i in responder_ids)}>{chairman_id}"


def get_or_create_prompt(user, role: str) -> Prompt:
    title, content = PROMPTS[role]
    prompt = Prompt.active_objects.filter(user=user, title=title).first()
    if prompt:
        return prompt
    return Prompt.active_objects.create(user=user, title=title, content=content)


def reset_prompts(user) -> int:
    """Rewrite the user's role prompts from code; returns how many changed."""
    changed = 0
    for role in PROMPTS:
        title, content = PROMPTS[role]
        prompt = Prompt.active_objects.filter(user=user, title=title).first()
        if prompt and prompt.content != content:
            prompt.content = content
            prompt.save(update_fields=["content"])
            changed += 1
    return changed


@dataclass
class EnsembleSpec:
    depth: str
    responders: List[LLM]
    chairman: Optional[LLM]
    title: str
    description: str = ""
    kind: str = WorkflowKind.USER
    signature: Optional[str] = None
    # Visible templates get a sample task so "Run" works straight away; chat
    # turns leave it empty and inject the person's message at run time.
    task_text: str = ""
    responder_flags: dict = field(default_factory=dict)


def _create_node(
    workflow: Workflow, node_id: str, node_type: str, data, x: float, y: float
) -> WorkflowNode:
    return WorkflowNode.objects.create(
        workflow=workflow,
        node_id=node_id,
        node_type=node_type,
        position_x=x,
        position_y=y,
        data_content_type=ContentType.objects.get_for_model(data),
        data_object_id=data.pk,
    )


# Builder nodes expose four numbered connectors on each side (``output-N`` on
# the start node, ``input-N`` on steps; a step's single output is ``default``).
# Spreading edges across them is what keeps a fan-in readable on the canvas.
HANDLE_SLOTS = 4


def _slot(index: int) -> int:
    return (index % HANDLE_SLOTS) + 1


def _create_edge(
    workflow: Workflow,
    source: str,
    target: str,
    source_handle: Optional[str] = None,
    target_handle: Optional[str] = None,
) -> WorkflowEdge:
    return WorkflowEdge.objects.create(
        workflow=workflow,
        edge_id=f"{source}->{target}",
        source=source,
        target=target,
        source_handle=source_handle,
        target_handle=target_handle,
    )


def _step(
    user,
    label: str,
    llm: LLM,
    role: str,
    task_text: str,
    use_previous_context: bool,
    **flags,
) -> StepNodeData:
    return StepNodeData.objects.create(
        label=label,
        llm=llm,
        prompt=get_or_create_prompt(user, role),
        text_input=task_text,
        use_previous_context=use_previous_context,
        **flags,
    )


COLUMN_GAP = 360.0
ROW_GAP = 170.0


def build_ensemble_workflow(user, spec: EnsembleSpec) -> Workflow:
    """Create the workflow rows for ``spec`` and return the workflow."""
    workflow = Workflow.objects.create(
        user=user,
        kind=spec.kind,
        ensemble_signature=spec.signature,
    )

    n = len(spec.responders)
    height = max(n - 1, 0) * ROW_GAP
    mid_y = height / 2

    start = StartNodeData.objects.create(
        title=spec.title,
        description=spec.description,
        mode=Mode.PARALLEL,
    )
    _create_node(workflow, START_NODE_ID, "start", start, 0.0, mid_y)

    if spec.depth == DEPTH_SINGLE:
        only = spec.responders[0]
        data = _step(user, only.name, only, DEPTH_SINGLE, spec.task_text, False)
        _create_node(workflow, responder_node_id(1), "step", data, COLUMN_GAP, mid_y)
        output = ChatOutputNodeData.objects.create(label=only.name)
        _create_node(
            workflow,
            CHAIRMAN_OUTPUT_NODE_ID,
            "chatOutput",
            output,
            COLUMN_GAP * 2,
            mid_y,
        )
        _create_edge(
            workflow, START_NODE_ID, responder_node_id(1), "output-1", "input-1"
        )
        _create_edge(
            workflow, responder_node_id(1), CHAIRMAN_OUTPUT_NODE_ID, "default", None
        )
        workflow.resolve_root_start_node()
        return workflow

    responder_ids = []
    for index, llm in enumerate(spec.responders, start=1):
        node_id = responder_node_id(index)
        data = _step(
            user,
            llm.name,
            llm,
            ROLE_RESPONDER,
            spec.task_text,
            False,
            **spec.responder_flags,
        )
        _create_node(workflow, node_id, "step", data, COLUMN_GAP, (index - 1) * ROW_GAP)
        _create_edge(
            workflow, START_NODE_ID, node_id, f"output-{_slot(index - 1)}", "input-1"
        )
        responder_ids.append(node_id)

    upstream_of_chairman = list(responder_ids)
    column = 2
    if spec.depth == DEPTH_COUNCIL:
        evaluator_ids = []
        for index, llm in enumerate(spec.responders, start=1):
            node_id = evaluator_node_id(index)
            data = _step(
                user, f"{llm.name} · review", llm, ROLE_EVALUATOR, spec.task_text, True
            )
            _create_node(
                workflow,
                node_id,
                "step",
                data,
                COLUMN_GAP * column,
                (index - 1) * ROW_GAP,
            )
            for slot, responder in enumerate(responder_ids):
                _create_edge(
                    workflow, responder, node_id, "default", f"input-{_slot(slot)}"
                )
            evaluator_ids.append(node_id)
        upstream_of_chairman.extend(evaluator_ids)
        column += 1

    chairman = spec.chairman or spec.responders[0]
    chairman_data = _step(
        user,
        f"Chairman · {chairman.name}",
        chairman,
        ROLE_CHAIRMAN,
        spec.task_text,
        True,
    )
    _create_node(
        workflow, CHAIRMAN_NODE_ID, "step", chairman_data, COLUMN_GAP * column, mid_y
    )
    # The chairman reads the drafts AND the reviews: a ranking alone cannot
    # be synthesized into an answer.
    for slot, upstream in enumerate(upstream_of_chairman):
        _create_edge(
            workflow, upstream, CHAIRMAN_NODE_ID, "default", f"input-{_slot(slot)}"
        )

    # A chat output shares its step's label; that pairing is how the builder
    # knows which panel to fill.
    output = ChatOutputNodeData.objects.create(label=chairman_data.label)
    _create_node(
        workflow,
        CHAIRMAN_OUTPUT_NODE_ID,
        "chatOutput",
        output,
        COLUMN_GAP * (column + 1),
        mid_y,
    )
    _create_edge(workflow, CHAIRMAN_NODE_ID, CHAIRMAN_OUTPUT_NODE_ID, "default", None)

    workflow.resolve_root_start_node()
    return workflow


def get_or_create_ensemble_workflow(
    user, depth: str, responders: List[LLM], chairman: LLM
) -> Workflow:
    """The hidden workflow behind a chat line-up, created on first use."""
    signature = ensemble_signature(depth, [llm.id for llm in responders], chairman.id)
    existing = (
        Workflow.active_objects.filter(
            user=user, kind=WorkflowKind.ENSEMBLE, ensemble_signature=signature
        )
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing

    names = ", ".join(llm.name for llm in responders)
    spec = EnsembleSpec(
        depth=depth,
        responders=responders,
        chairman=chairman,
        title=f"{depth.capitalize()} · {names}",
        description=(
            f"Compiled from the chat model picker. Every {depth} turn with this "
            f"line-up runs this workflow; {chairman.name} chairs."
        ),
        kind=WorkflowKind.ENSEMBLE,
        signature=signature,
    )
    return build_ensemble_workflow(user, spec)
