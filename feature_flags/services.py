"""
Feature flag resolution. Precedence: user override > group override > flag default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from feature_flags.models import (
    FeatureFlag,
    GroupFeatureOverride,
    UserFeatureOverride,
)

if TYPE_CHECKING:
    from users.models import User


def is_flag_enabled_for_user(user: "User", key: str) -> bool:
    """
    Resolve a single flag's effective value for ``user`` using the same
    precedence as :func:`resolve_flags_for_user` (user > group > default).

    Returns ``False`` if the flag does not exist — callers can rely on this
    as a safe default for code paths that pre-date the flag definition.
    """
    flag = FeatureFlag.objects.filter(key=key).only("id", "default_enabled").first()
    if flag is None:
        return False

    user_override = (
        UserFeatureOverride.objects.filter(user_id=user.id, flag_id=flag.id)
        .values_list("enabled", flat=True)
        .first()
    )
    if user_override is not None:
        return bool(user_override)

    group_id = getattr(user, "access_code_group_id", None)
    if group_id is not None:
        group_override = (
            GroupFeatureOverride.objects.filter(group_id=group_id, flag_id=flag.id)
            .values_list("enabled", flat=True)
            .first()
        )
        if group_override is not None:
            return bool(group_override)

    return bool(flag.default_enabled)


def resolve_flags_for_user(user: "User") -> Dict[str, bool]:
    """
    Return the effective {key: enabled} map for ``user``.

    Three queries: all flags, this user's overrides, this user's group's overrides.
    """
    flags = list(FeatureFlag.objects.all().only("id", "key", "default_enabled"))
    if not flags:
        return {}

    flag_id_to_key = {f.id: f.key for f in flags}
    resolved: Dict[str, bool] = {f.key: f.default_enabled for f in flags}

    group_id = getattr(user, "access_code_group_id", None)
    if group_id is not None:
        group_overrides = GroupFeatureOverride.objects.filter(
            group_id=group_id,
            flag_id__in=flag_id_to_key.keys(),
        ).values_list("flag_id", "enabled")
        for flag_id, enabled in group_overrides:
            key = flag_id_to_key.get(flag_id)
            if key is not None:
                resolved[key] = enabled

    user_overrides = UserFeatureOverride.objects.filter(
        user_id=user.id,
        flag_id__in=flag_id_to_key.keys(),
    ).values_list("flag_id", "enabled")
    for flag_id, enabled in user_overrides:
        key = flag_id_to_key.get(flag_id)
        if key is not None:
            resolved[key] = enabled

    return resolved
