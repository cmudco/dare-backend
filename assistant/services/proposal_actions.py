"""Applying and undoing each kind of proposed change.

Every ``apply`` returns a journal of exactly what it changed (skipping what
was already in place) plus notes for the user; ``undo`` reverts only what its
journal recorded, so applying and undoing can go back and forth safely.
Deletes are soft: the rows stay and undo restores them.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from django.db.models import Q
from django.utils import timezone

from assistant.constants import ProposalActionType as Type
from conversations.constants import ConversationSource
from conversations.models import Conversation
from files.models import File, Folder, Tag
from projects.models import PersonalProject
from projects.services.project_service import touch_project

Action = Dict[str, Any]
Journal = Dict[str, Any]
Notes = List[str]


def live_files(user, ids):
    return File.active_objects.filter(user=user, is_media=False, id__in=ids)


def live_chats(user, conversation_ids):
    return Conversation.active_objects.filter(
        user=user,
        source=ConversationSource.DARE,
        conversation_id__in=conversation_ids,
    )


def find_folder(user, name: str) -> Optional[Folder]:
    return Folder.objects.filter(user=user, name__iexact=name).first()


def find_tag(user, label: str) -> Optional[Tag]:
    return Tag.objects.filter(Q(user=user) | Q(user=None), label__iexact=label).first()


def find_project(user, name: str) -> Optional[PersonalProject]:
    return PersonalProject.active_objects.filter(user=user, name__iexact=name).first()


def _file_ids(action: Action) -> List[int]:
    return [file["id"] for file in action["files"]]


def _chat_ids(action: Action) -> List[str]:
    return [chat["id"] for chat in action["conversations"]]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _gone_notes(action: Action, found_files: int, found_chats: int = 0) -> Notes:
    notes = []
    missing_files = len(action["files"]) - found_files
    missing_chats = len(action["conversations"]) - found_chats
    if missing_files:
        notes.append(f"{_plural(missing_files, 'file')} no longer exist.")
    if missing_chats:
        notes.append(f"{_plural(missing_chats, 'chat')} no longer exist.")
    return notes


def _has_content(project: PersonalProject) -> bool:
    return (
        Conversation.active_objects.filter(project=project).exists()
        or project.files.exists()
        or project.folders.exists()
        or project.libraries.exists()
        or project.workflows.exists()
    )


# --- Folders -----------------------------------------------------------------


def _apply_add_to_folder(user, action: Action) -> Tuple[Journal, Notes]:
    folder = find_folder(user, action["name"])
    created = folder is None
    if created:
        folder = Folder.objects.create(user=user, name=action["name"])
    ids = set(live_files(user, _file_ids(action)).values_list("id", flat=True))
    already = set(folder.files.filter(id__in=ids).values_list("id", flat=True))
    added = sorted(ids - already)
    folder.files.add(*added)
    notes = _gone_notes(action, len(ids))
    if already:
        notes.append(f"{_plural(len(already), 'file')} already in this folder.")
    return {"folder_id": folder.id, "created": created, "added": added}, notes


def _undo_add_to_folder(user, journal: Journal) -> Notes:
    folder = Folder.objects.filter(user=user, pk=journal["folder_id"]).first()
    if folder is None:
        return ["The folder no longer exists."]
    folder.files.remove(*journal["added"])
    if (
        journal["created"]
        and not folder.files.exists()
        and not folder.personal_projects.exists()
    ):
        folder.delete()
    return []


def _apply_remove_from_folder(user, action: Action) -> Tuple[Journal, Notes]:
    folder = find_folder(user, action["name"])
    if folder is None:
        return {}, ["The folder no longer exists."]
    removed = sorted(
        folder.files.filter(id__in=_file_ids(action)).values_list("id", flat=True)
    )
    folder.files.remove(*removed)
    notes = []
    if len(removed) < len(action["files"]):
        notes.append(
            f"{_plural(len(action['files']) - len(removed), 'file')} "
            "were not in this folder."
        )
    return {"folder_id": folder.id, "removed": removed}, notes


def _undo_remove_from_folder(user, journal: Journal) -> Notes:
    folder = Folder.objects.filter(user=user, pk=journal["folder_id"]).first()
    if folder is None:
        return ["The folder no longer exists."]
    folder.files.add(*File._base_manager.filter(user=user, id__in=journal["removed"]))
    return []


# --- Tags --------------------------------------------------------------------


def _apply_add_tag(user, action: Action) -> Tuple[Journal, Notes]:
    tag = find_tag(user, action["name"])
    created = tag is None
    if created:
        # Tag labels are unique across all accounts.
        if Tag.objects.filter(label__iexact=action["name"]).exists():
            return {}, [f'The tag name "{action["name"]}" is unavailable.']
        tag = Tag.objects.create(user=user, label=action["name"])
    ids = set(live_files(user, _file_ids(action)).values_list("id", flat=True))
    already = set(tag.files.filter(id__in=ids).values_list("id", flat=True))
    added = sorted(ids - already)
    tag.files.add(*added)
    notes = _gone_notes(action, len(ids))
    if already:
        notes.append(f"{_plural(len(already), 'file')} already had this tag.")
    return {"tag_id": tag.id, "created": created, "added": added}, notes


def _undo_add_tag(user, journal: Journal) -> Notes:
    tag = Tag.objects.filter(Q(user=user) | Q(user=None), pk=journal["tag_id"]).first()
    if tag is None:
        return ["The tag no longer exists."]
    tag.files.remove(*journal["added"])
    if journal["created"] and tag.user_id == user.id and not tag.files.exists():
        tag.delete()
    return []


def _apply_remove_tag(user, action: Action) -> Tuple[Journal, Notes]:
    tag = find_tag(user, action["name"])
    if tag is None:
        return {}, ["The tag no longer exists."]
    removed = sorted(
        tag.files.filter(user=user, id__in=_file_ids(action)).values_list(
            "id", flat=True
        )
    )
    tag.files.remove(*removed)
    notes = []
    if len(removed) < len(action["files"]):
        notes.append(
            f"{_plural(len(action['files']) - len(removed), 'file')} "
            "did not have this tag."
        )
    return {"tag_id": tag.id, "removed": removed}, notes


def _undo_remove_tag(user, journal: Journal) -> Notes:
    tag = Tag.objects.filter(Q(user=user) | Q(user=None), pk=journal["tag_id"]).first()
    if tag is None:
        return ["The tag no longer exists."]
    tag.files.add(*File._base_manager.filter(user=user, id__in=journal["removed"]))
    return []


# --- Files and chats ---------------------------------------------------------


def _apply_delete_files(user, action: Action) -> Tuple[Journal, Notes]:
    files = live_files(user, _file_ids(action))
    deleted = sorted(files.values_list("id", flat=True))
    # updated_at doubles as the deletion time in Recently deleted.
    File._base_manager.filter(user=user, id__in=deleted).update(
        is_deleted=True, updated_at=timezone.now()
    )
    return {"deleted": deleted}, _gone_notes(action, len(deleted))


def _undo_delete_files(user, journal: Journal) -> Notes:
    restored = File._base_manager.filter(user=user, id__in=journal["deleted"]).update(
        is_deleted=False, updated_at=timezone.now()
    )
    purged = len(journal["deleted"]) - restored
    if purged:
        return [f"{_plural(purged, 'file')} already deleted permanently."]
    return []


def _apply_delete_conversations(user, action: Action) -> Tuple[Journal, Notes]:
    chats = live_chats(user, _chat_ids(action))
    deleted = sorted(chats.values_list("id", flat=True))
    Conversation._base_manager.filter(user=user, id__in=deleted).update(is_deleted=True)
    return {"deleted": deleted}, _gone_notes(action, 0, len(deleted))


def _undo_delete_conversations(user, journal: Journal) -> Notes:
    Conversation._base_manager.filter(user=user, id__in=journal["deleted"]).update(
        is_deleted=False
    )
    return []


# --- Projects ----------------------------------------------------------------


def _apply_create_project(user, action: Action) -> Tuple[Journal, Notes]:
    if find_project(user, action["name"]) is not None:
        return {}, ["A project with this name already exists."]
    project = PersonalProject.active_objects.create(
        user=user, name=action["name"], description=action["description"]
    )
    return {"project_id": project.id}, []


def _undo_create_project(user, journal: Journal) -> Notes:
    project = PersonalProject.active_objects.filter(
        user=user, pk=journal["project_id"]
    ).first()
    if project is None:
        return []
    if _has_content(project):
        return ["The project was kept because it now has chats or sources."]
    project.soft_delete()
    return []


def _apply_add_to_project(user, action: Action) -> Tuple[Journal, Notes]:
    project = find_project(user, action["name"])
    created = project is None
    if created:
        project = PersonalProject.active_objects.create(user=user, name=action["name"])
    file_ids = set(live_files(user, _file_ids(action)).values_list("id", flat=True))
    already = set(project.files.filter(id__in=file_ids).values_list("id", flat=True))
    files_added = sorted(file_ids - already)
    project.files.add(*files_added)
    chats = list(live_chats(user, _chat_ids(action)))
    moved = [
        [chat.id, chat.project_id] for chat in chats if chat.project_id != project.id
    ]
    Conversation._base_manager.filter(user=user, id__in=[pk for pk, _ in moved]).update(
        project=project
    )
    touch_project(project)
    notes = _gone_notes(action, len(file_ids), len(chats))
    if len(moved) < len(chats):
        notes.append(
            f"{_plural(len(chats) - len(moved), 'chat')} already in this project."
        )
    return {
        "project_id": project.id,
        "created": created,
        "files_added": files_added,
        "chats_moved": moved,
    }, notes


def _undo_add_to_project(user, journal: Journal) -> Notes:
    project = PersonalProject.active_objects.filter(
        user=user, pk=journal["project_id"]
    ).first()
    if project is None:
        return ["The project no longer exists."]
    project.files.remove(*journal["files_added"])
    live_projects = set(
        PersonalProject.active_objects.filter(
            user=user, pk__in=[previous for _, previous in journal["chats_moved"]]
        ).values_list("id", flat=True)
    )
    for chat_pk, previous in journal["chats_moved"]:
        Conversation._base_manager.filter(
            user=user, pk=chat_pk, project_id=project.id
        ).update(project_id=previous if previous in live_projects else None)
    if journal["created"] and not _has_content(project):
        project.soft_delete()
    return []


def _apply_remove_from_project(user, action: Action) -> Tuple[Journal, Notes]:
    project = find_project(user, action["name"])
    if project is None:
        return {}, ["The project no longer exists."]
    files_removed = sorted(
        project.files.filter(id__in=_file_ids(action)).values_list("id", flat=True)
    )
    project.files.remove(*files_removed)
    chats_removed = sorted(
        live_chats(user, _chat_ids(action))
        .filter(project=project)
        .values_list("id", flat=True)
    )
    Conversation._base_manager.filter(user=user, id__in=chats_removed).update(
        project=None
    )
    notes = []
    skipped = (
        len(action["files"])
        + len(action["conversations"])
        - len(files_removed)
        - len(chats_removed)
    )
    if skipped:
        notes.append(f"{_plural(skipped, 'item')} were not in this project.")
    return {
        "project_id": project.id,
        "files_removed": files_removed,
        "chats_removed": chats_removed,
    }, notes


def _undo_remove_from_project(user, journal: Journal) -> Notes:
    project = PersonalProject.active_objects.filter(
        user=user, pk=journal["project_id"]
    ).first()
    if project is None:
        return ["The project no longer exists."]
    project.files.add(
        *File._base_manager.filter(user=user, id__in=journal["files_removed"])
    )
    Conversation._base_manager.filter(
        user=user, pk__in=journal["chats_removed"], project=None
    ).update(project=project)
    return []


def _apply_delete_project(user, action: Action) -> Tuple[Journal, Notes]:
    project = find_project(user, action["name"])
    if project is None:
        return {}, ["The project no longer exists."]
    released = sorted(
        Conversation.active_objects.filter(project=project).values_list("id", flat=True)
    )
    Conversation._base_manager.filter(user=user, id__in=released).update(project=None)
    project.soft_delete()
    notes = []
    if released:
        notes.append(f"{_plural(len(released), 'chat')} moved back to your chat list.")
    return {"project_id": project.id, "chats_released": released}, notes


def _undo_delete_project(user, journal: Journal) -> Notes:
    project = PersonalProject.objects.filter(
        user=user, pk=journal["project_id"]
    ).first()
    if project is None:
        return ["The project no longer exists."]
    project.undelete()
    Conversation._base_manager.filter(
        user=user, pk__in=journal["chats_released"], project=None
    ).update(project=project)
    return []


HANDLERS: Dict[
    str,
    Tuple[
        Callable[[Any, Action], Tuple[Journal, Notes]], Callable[[Any, Journal], Notes]
    ],
] = {
    Type.ADD_TO_FOLDER: (_apply_add_to_folder, _undo_add_to_folder),
    Type.REMOVE_FROM_FOLDER: (_apply_remove_from_folder, _undo_remove_from_folder),
    Type.ADD_TAG: (_apply_add_tag, _undo_add_tag),
    Type.REMOVE_TAG: (_apply_remove_tag, _undo_remove_tag),
    Type.DELETE_FILES: (_apply_delete_files, _undo_delete_files),
    Type.CREATE_PROJECT: (_apply_create_project, _undo_create_project),
    Type.ADD_TO_PROJECT: (_apply_add_to_project, _undo_add_to_project),
    Type.REMOVE_FROM_PROJECT: (_apply_remove_from_project, _undo_remove_from_project),
    Type.DELETE_PROJECT: (_apply_delete_project, _undo_delete_project),
    Type.DELETE_CONVERSATIONS: (
        _apply_delete_conversations,
        _undo_delete_conversations,
    ),
}
