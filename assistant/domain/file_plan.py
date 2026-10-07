"""Validation of a model-written file organisation plan.

The model's arguments are untrusted: this turns them into a bounded, deduped
plan or a list of errors the model can read and correct.
"""

from dataclasses import dataclass
from typing import Any, List, Tuple

from assistant.constants import PLAN_MAX_GROUPS, PLAN_NAME_MAX_LENGTH


@dataclass(frozen=True)
class PlanGroup:
    name: str
    file_ids: Tuple[int, ...]


@dataclass(frozen=True)
class FilePlan:
    summary: str
    folders: Tuple[PlanGroup, ...]
    tags: Tuple[PlanGroup, ...]

    @property
    def file_ids(self) -> frozenset:
        return frozenset(
            file_id for group in self.folders + self.tags for file_id in group.file_ids
        )


def parse_file_plan(arguments: dict) -> Tuple[FilePlan, List[str]]:
    errors: List[str] = []
    folders = _groups(arguments.get("folders"), "folders", "name", errors)
    tags = _groups(arguments.get("tags"), "tags", "label", errors)
    if not folders and not tags and not errors:
        errors.append("The plan has no folders or tags.")
    summary = str(arguments.get("summary") or "").strip()[:500]
    return FilePlan(summary=summary, folders=folders, tags=tags), errors


def _groups(raw: Any, field: str, name_key: str, errors: List[str]):
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(f"`{field}` must be a list.")
        return ()
    if len(raw) > PLAN_MAX_GROUPS:
        errors.append(f"At most {PLAN_MAX_GROUPS} {field} per plan.")
        return ()
    merged = {}
    for entry in raw:
        if not isinstance(entry, dict):
            errors.append(f"Each of `{field}` must be an object.")
            continue
        name = str(entry.get(name_key) or "").strip()
        ids = entry.get("file_ids")
        if not name or len(name) > PLAN_NAME_MAX_LENGTH:
            errors.append(f"Each of `{field}` needs a {name_key} of 1-255 characters.")
            continue
        if not isinstance(ids, list) or not ids:
            errors.append(f"'{name}' needs a non-empty file_ids list.")
            continue
        if not all(type(file_id) is int for file_id in ids):
            errors.append(f"'{name}' has file_ids that are not integers.")
            continue
        # Same name twice (any case) is one group.
        key = name.casefold()
        existing_name, existing_ids = merged.get(key, (name, ()))
        merged[key] = (existing_name, existing_ids + tuple(ids))
    return tuple(
        PlanGroup(name=name, file_ids=tuple(dict.fromkeys(ids)))
        for name, ids in merged.values()
    )
