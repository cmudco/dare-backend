"""Validation of a model-written change plan.

The model's arguments are untrusted: this turns them into a bounded list of
typed actions or a list of errors the model can read and correct.
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from assistant.constants import PLAN_MAX_ACTIONS, PLAN_MAX_ITEMS, PLAN_NAME_MAX_LENGTH
from assistant.constants import ProposalActionType as Type
from projects.constants import PROJECT_DESCRIPTION_MAX_LENGTH, PROJECT_NAME_MAX_LENGTH

NAMED = {
    Type.ADD_TO_FOLDER,
    Type.REMOVE_FROM_FOLDER,
    Type.ADD_TAG,
    Type.REMOVE_TAG,
    Type.CREATE_PROJECT,
    Type.ADD_TO_PROJECT,
    Type.REMOVE_FROM_PROJECT,
    Type.DELETE_PROJECT,
}
FILES_ONLY = {
    Type.ADD_TO_FOLDER,
    Type.REMOVE_FROM_FOLDER,
    Type.ADD_TAG,
    Type.REMOVE_TAG,
    Type.DELETE_FILES,
}
PROJECT_MEMBERSHIP = {Type.ADD_TO_PROJECT, Type.REMOVE_FROM_PROJECT}
PROJECT_NAMED = {
    Type.CREATE_PROJECT,
    Type.ADD_TO_PROJECT,
    Type.REMOVE_FROM_PROJECT,
    Type.DELETE_PROJECT,
}


@dataclass(frozen=True)
class PlannedAction:
    type: str
    name: str
    description: str
    file_ids: Tuple[int, ...]
    conversation_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ChangePlan:
    summary: str
    actions: Tuple[PlannedAction, ...]

    @property
    def file_ids(self) -> frozenset:
        return frozenset(i for action in self.actions for i in action.file_ids)

    @property
    def conversation_ids(self) -> frozenset:
        return frozenset(i for action in self.actions for i in action.conversation_ids)


def parse_change_plan(arguments: dict) -> Tuple[ChangePlan, List[str]]:
    errors: List[str] = []
    raw = arguments.get("actions")
    actions: List[PlannedAction] = []
    if not isinstance(raw, list) or not raw:
        errors.append("`actions` must be a non-empty list.")
    elif len(raw) > PLAN_MAX_ACTIONS:
        errors.append(f"At most {PLAN_MAX_ACTIONS} actions per plan.")
    else:
        for index, entry in enumerate(raw, start=1):
            action = _action(entry, f"Action {index}", errors)
            if action is not None:
                actions.append(action)
    summary = str(arguments.get("summary") or "").strip()[:500]
    return ChangePlan(summary=summary, actions=tuple(actions)), errors


def _action(entry: Any, label: str, errors: List[str]) -> Optional[PlannedAction]:
    if not isinstance(entry, dict):
        errors.append(f"{label} must be an object.")
        return None
    kind = entry.get("type")
    if kind not in Type.values:
        errors.append(f"{label} has unknown type {kind!r}.")
        return None
    label = f"{label} ({kind})"
    name = str(entry.get("name") or "").strip()
    max_name = (
        PROJECT_NAME_MAX_LENGTH if kind in PROJECT_NAMED else PLAN_NAME_MAX_LENGTH
    )
    if kind in NAMED and not 0 < len(name) <= max_name:
        errors.append(f"{label} needs a name of 1-{max_name} characters.")
        return None
    file_ids = _ids(entry.get("file_ids"), int, f"{label} file_ids", errors)
    conversation_ids = _ids(
        entry.get("conversation_ids"), str, f"{label} conversation_ids", errors
    )
    if file_ids is None or conversation_ids is None:
        return None
    if kind in FILES_ONLY and (not file_ids or conversation_ids):
        errors.append(f"{label} needs file_ids and no conversation_ids.")
        return None
    if kind in PROJECT_MEMBERSHIP and not (file_ids or conversation_ids):
        errors.append(f"{label} needs file_ids or conversation_ids.")
        return None
    if kind == Type.DELETE_CONVERSATIONS and (not conversation_ids or file_ids):
        errors.append(f"{label} needs conversation_ids and no file_ids.")
        return None
    if kind in (Type.CREATE_PROJECT, Type.DELETE_PROJECT) and (
        file_ids or conversation_ids
    ):
        errors.append(f"{label} takes only a name.")
        return None
    description = ""
    if kind == Type.CREATE_PROJECT:
        description = str(entry.get("description") or "").strip()
        description = description[:PROJECT_DESCRIPTION_MAX_LENGTH]
    return PlannedAction(
        type=kind,
        name=name if kind in NAMED else "",
        description=description,
        file_ids=file_ids,
        conversation_ids=conversation_ids,
    )


def _ids(raw: Any, kind: type, label: str, errors: List[str]):
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(f"{label} must be a list.")
        return None
    if len(raw) > PLAN_MAX_ITEMS:
        errors.append(f"{label} has more than {PLAN_MAX_ITEMS} ids.")
        return None
    if not all(type(item) is kind and item != "" for item in raw):
        errors.append(f"{label} must contain only {kind.__name__} ids.")
        return None
    return tuple(dict.fromkeys(raw))
