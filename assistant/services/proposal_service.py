"""Change proposals: recorded by the assistant, applied and undone by the user.

The assistant can only *propose*. Applying, undoing, discarding and restoring
are separate user requests; each action can go back and forth any number of
times because undo reverts exactly what the last apply journaled.
"""

from typing import Any, Dict, Iterable, List, Optional

from django.db import transaction
from django.utils import timezone

from assistant.constants import ProposalActionStatus
from assistant.constants import ProposalActionType as Type
from assistant.constants import ProposalStatus
from assistant.domain.change_plan import PlannedAction, parse_change_plan
from assistant.models import AssistantProposal
from assistant.services.proposal_actions import (
    HANDLERS,
    find_folder,
    find_project,
    find_tag,
    live_chats,
    live_files,
)
from files.models import Tag


class ProposalNotFound(Exception):
    pass


class ProposalConflict(Exception):
    """The request does not fit the proposal's current state."""


_EXISTING_TARGET = {
    Type.REMOVE_FROM_FOLDER: ("folder", find_folder),
    Type.REMOVE_TAG: ("tag", find_tag),
    Type.REMOVE_FROM_PROJECT: ("project", find_project),
    Type.DELETE_PROJECT: ("project", find_project),
}
_NEW_TARGET = {
    Type.ADD_TO_FOLDER: find_folder,
    Type.ADD_TAG: find_tag,
    Type.ADD_TO_PROJECT: find_project,
    Type.CREATE_PROJECT: find_project,
}


def propose_changes(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the model's plan against the user's data and record it."""
    plan, errors = parse_change_plan(arguments)
    if errors:
        return {"success": False, "error": " ".join(errors)}
    file_names = dict(live_files(user, plan.file_ids).values_list("id", "name"))
    chat_titles = dict(
        live_chats(user, plan.conversation_ids).values_list("conversation_id", "title")
    )
    errors = _reference_errors(user, plan.actions, file_names, chat_titles)
    if errors:
        return {"success": False, "error": " ".join(errors)}
    actions = [
        _stored(index, action, user, file_names, chat_titles)
        for index, action in enumerate(plan.actions, start=1)
    ]
    proposal = AssistantProposal.active_objects.create(
        user=user,
        summary=plan.summary or "Proposed changes",
        plan={"actions": actions},
    )
    return {
        "success": True,
        "proposal_id": proposal.id,
        "actions": len(actions),
        "deletes": sum(action["type"].startswith("delete") for action in actions),
    }


def _reference_errors(user, actions, file_names, chat_titles) -> List[str]:
    errors = []
    unknown_files = sorted({i for a in actions for i in a.file_ids} - file_names.keys())
    if unknown_files:
        errors.append(
            f"Unknown file ids {unknown_files}. Use only ids from list_my_files."
        )
    unknown_chats = sorted(
        {i for a in actions for i in a.conversation_ids} - chat_titles.keys()
    )
    if unknown_chats:
        errors.append(
            f"Unknown conversation ids {unknown_chats}. "
            "Use only ids from list_my_conversations."
        )
    for action in actions:
        if action.type in _EXISTING_TARGET:
            noun, find = _EXISTING_TARGET[action.type]
            if find(user, action.name) is None:
                errors.append(f'There is no {noun} named "{action.name}".')
        if action.type == Type.CREATE_PROJECT and find_project(user, action.name):
            errors.append(
                f'A project named "{action.name}" already exists; '
                "use add_to_project instead."
            )
        if (
            action.type == Type.ADD_TAG
            and find_tag(user, action.name) is None
            and Tag.objects.filter(label__iexact=action.name).exists()
        ):
            errors.append(f'The tag name "{action.name}" is taken; pick another.')
    return errors


def _stored(index, action: PlannedAction, user, file_names, chat_titles) -> Dict:
    find_new = _NEW_TARGET.get(action.type)
    return {
        "id": str(index),
        "type": action.type,
        "name": action.name,
        "is_new": bool(find_new) and find_new(user, action.name) is None,
        "description": action.description,
        "files": [{"id": i, "name": file_names[i]} for i in action.file_ids],
        "conversations": [
            {"id": i, "title": chat_titles[i]} for i in action.conversation_ids
        ],
        "status": ProposalActionStatus.PENDING,
        "notes": [],
        "journal": None,
    }


def _locked(user, proposal_id: int) -> AssistantProposal:
    proposal = (
        AssistantProposal.active_objects.select_for_update()
        .filter(user=user, pk=proposal_id)
        .first()
    )
    if proposal is None:
        raise ProposalNotFound()
    return proposal


def _selected(proposal, action_ids: Optional[Iterable[str]], status: str):
    wanted = None if action_ids is None else set(action_ids)
    return [
        action
        for action in proposal.plan["actions"]
        if action["status"] == status and (wanted is None or action["id"] in wanted)
    ]


def _save(proposal: AssistantProposal) -> AssistantProposal:
    statuses = {action["status"] for action in proposal.plan["actions"]}
    if statuses == {ProposalActionStatus.APPLIED}:
        proposal.status = ProposalStatus.APPLIED
    elif ProposalActionStatus.APPLIED in statuses:
        proposal.status = ProposalStatus.PARTIALLY_APPLIED
    else:
        proposal.status = ProposalStatus.PENDING
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["plan", "status", "decided_at", "updated_at"])
    return proposal


@transaction.atomic
def apply_actions(
    user, proposal_id: int, action_ids: Optional[List[str]] = None
) -> AssistantProposal:
    """Apply the chosen pending actions (all of them by default), in plan order."""
    proposal = _locked(user, proposal_id)
    if proposal.status == ProposalStatus.DISCARDED:
        raise ProposalConflict("Restore this proposal before applying it.")
    for action in _selected(proposal, action_ids, ProposalActionStatus.PENDING):
        apply, _ = HANDLERS[action["type"]]
        journal, notes = apply(user, action)
        action.update(status=ProposalActionStatus.APPLIED, journal=journal, notes=notes)
    return _save(proposal)


@transaction.atomic
def undo_actions(
    user, proposal_id: int, action_ids: Optional[List[str]] = None
) -> AssistantProposal:
    """Revert the chosen applied actions (all by default), newest effect first."""
    proposal = _locked(user, proposal_id)
    for action in reversed(
        _selected(proposal, action_ids, ProposalActionStatus.APPLIED)
    ):
        _, undo = HANDLERS[action["type"]]
        journal = action["journal"]
        notes = undo(user, journal) if journal else ["Nothing to undo."]
        action.update(status=ProposalActionStatus.PENDING, journal=None, notes=notes)
    return _save(proposal)


@transaction.atomic
def discard_proposal(user, proposal_id: int) -> AssistantProposal:
    proposal = _locked(user, proposal_id)
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalConflict("Undo the applied changes before discarding.")
    proposal.status = ProposalStatus.DISCARDED
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["status", "decided_at", "updated_at"])
    return proposal


@transaction.atomic
def restore_proposal(user, proposal_id: int) -> AssistantProposal:
    proposal = _locked(user, proposal_id)
    if proposal.status != ProposalStatus.DISCARDED:
        raise ProposalConflict("Only a discarded proposal can be restored.")
    return _save(proposal)
