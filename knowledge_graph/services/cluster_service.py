"""Owns entity identity: which keys are one thing for this user, and why."""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from django.db import transaction

from core.services.decisions_service import DecisionsService, Question, batched
from files.models import DocumentEntity
from knowledge_graph.constants import (
    ALIAS_MERGE_THRESHOLD,
    ALIAS_QUESTIONS_PER_REQUEST,
    LINKING_EXCLUDED_KINDS,
    MAX_ALIAS_CANDIDATES,
    AliasSource,
)
from knowledge_graph.domain.alias_rules import (
    NamedKey,
    alias_candidates,
    blocking_key,
    clean_display,
    display_name,
    rule_merges,
)
from knowledge_graph.models import (
    AliasDecision,
    ClusterAlias,
    EntityCluster,
    EntityRelation,
)

logger = logging.getLogger(__name__)

CONTEXT_CHARS = 180
ALIAS_INPUT = (
    "You decide whether two names found in a user's documents refer to the same "
    "real-world entity. A season, sequel, or sub-part is a different entity from "
    "the whole work. Two people sharing a surname are different people unless the "
    "context says otherwise."
)


@dataclass(frozen=True)
class KeyStats:
    key: str
    text: str
    kind: str
    weight: int


def _ordered(key_a: str, key_b: str) -> Tuple[str, str]:
    return (key_a, key_b) if key_a < key_b else (key_b, key_a)


class EntityClusterService:
    @staticmethod
    def _user_entities(user):
        return DocumentEntity.objects.filter(
            file__user=user, file__is_deleted=False, file__is_active=True
        ).exclude(kind__in=LINKING_EXCLUDED_KINDS)

    @classmethod
    def _key_stats(cls, user) -> List[KeyStats]:
        kinds: Dict[str, Counter] = defaultdict(Counter)
        texts: Dict[str, Counter] = defaultdict(Counter)
        for row in cls._user_entities(user).values("key", "kind", "text", "mentions"):
            kinds[row["key"]][row["kind"]] += row["mentions"]
            texts[row["key"]][row["text"]] += row["mentions"]
        return [
            KeyStats(
                key=key,
                text=texts[key].most_common(1)[0][0],
                kind=kinds[key].most_common(1)[0][0],
                weight=sum(kinds[key].values()),
            )
            for key in sorted(kinds)
        ]

    @classmethod
    @transaction.atomic
    def sync_user(cls, user) -> int:
        """Give every new key a cluster; idempotent. Returns how many keys were new.

        Only the free name rule merges here. Anything needing judgement waits
        for ``adjudicate``, which spends money only inside an approved build.
        """
        known = {
            alias.key: alias
            for alias in ClusterAlias.objects.filter(user=user).select_related(
                "cluster"
            )
        }
        by_block: Dict[str, EntityCluster] = {}
        for key in sorted(known):
            by_block.setdefault(blocking_key(key), known[key].cluster)
        refused = {
            (d.key_a, d.key_b)
            for d in AliasDecision.objects.filter(user=user, same=False)
        }
        created = 0
        for stats in cls._key_stats(user):
            if stats.key in known:
                continue
            block = blocking_key(stats.key)
            cluster = by_block.get(block)
            if cluster is not None and cls._blocked(refused, stats.key, cluster, known):
                cluster = None
            source = AliasSource.RULE if cluster is not None else AliasSource.SELF
            if cluster is None:
                cluster = EntityCluster.objects.create(
                    user=user, label=stats.text[:200], kind=stats.kind
                )
                by_block.setdefault(block, cluster)
            known[stats.key] = ClusterAlias.objects.create(
                user=user,
                cluster=cluster,
                key=stats.key,
                text=stats.text[:200],
                source=source,
            )
            created += 1
        cls._apply_rule_merges(user, refused)
        return created

    @classmethod
    def _apply_rule_merges(cls, user, refused) -> None:
        stats = cls._key_stats(user)
        for short, owner in rule_merges(
            NamedKey(item.key, item.text, item.kind, item.weight) for item in stats
        ):
            aliases = {
                alias.key: alias
                for alias in ClusterAlias.objects.filter(
                    user=user, key__in=[short, owner]
                ).select_related("cluster")
            }
            if short not in aliases or owner not in aliases:
                continue
            keep, drop = aliases[owner].cluster, aliases[short].cluster
            if keep.pk == drop.pk or cls._clusters_refused(refused, keep, drop):
                continue
            cls._absorb(keep, drop, AliasSource.RULE, None)

    @staticmethod
    def _clusters_refused(refused, first, second) -> bool:
        first_keys = list(first.aliases.values_list("key", flat=True))
        second_keys = list(second.aliases.values_list("key", flat=True))
        return any(_ordered(a, b) in refused for a in first_keys for b in second_keys)

    @staticmethod
    def _blocked(refused, key, cluster, known) -> bool:
        return any(
            _ordered(key, other) in refused
            for other, alias in known.items()
            if alias.cluster_id == cluster.pk
        )

    @classmethod
    def refresh_labels(cls, user) -> None:
        """Label each cluster with its most used spelling and kind."""
        stats = {item.key: item for item in cls._key_stats(user)}
        aliases: Dict[int, List[str]] = defaultdict(list)
        for key, cluster_id in ClusterAlias.objects.filter(user=user).values_list(
            "key", "cluster_id"
        ):
            if key in stats:
                aliases[cluster_id].append(key)
        clusters = list(EntityCluster.objects.filter(user=user, id__in=aliases))
        for cluster in clusters:
            members = [stats[key] for key in aliases[cluster.pk]]
            cluster.label = clean_display(
                display_name(
                    [NamedKey(m.key, m.text, m.kind, m.weight) for m in members]
                ).text
            )[:200]
            kinds: Counter = Counter()
            for member in members:
                kinds[member.kind] += member.weight
            cluster.kind = kinds.most_common(1)[0][0]
        EntityCluster.objects.bulk_update(clusters, ["label", "kind"])

    @classmethod
    def adjudicate(
        cls, user, file_ids: Sequence[int], client: DecisionsService
    ) -> Tuple[int, int]:
        """Ask the model about unclear pairs; returns (questions, merges)."""
        keys = list(
            cls._user_entities(user)
            .filter(file_id__in=file_ids)
            .values_list("key", flat=True)
            .distinct()
        )
        decided = set(
            AliasDecision.objects.filter(user=user).values_list("key_a", "key_b")
        )
        aliases = {
            alias.key: alias
            for alias in ClusterAlias.objects.filter(user=user, key__in=keys)
        }
        candidates = [
            candidate
            for candidate in alias_candidates(keys)
            if (candidate.key_a, candidate.key_b) not in decided
            and candidate.key_a in aliases
            and candidate.key_b in aliases
            and aliases[candidate.key_a].cluster_id
            != aliases[candidate.key_b].cluster_id
        ][:MAX_ALIAS_CANDIDATES]
        if not candidates:
            return 0, 0
        contexts = cls._contexts(
            user, file_ids, {k for c in candidates for k in (c.key_a, c.key_b)}
        )
        asked = merges = 0
        for batch in batched(candidates, ALIAS_QUESTIONS_PER_REQUEST):
            questions = [
                Question(
                    name=f"pair_{index}",
                    instructions=(
                        f"Do '{aliases[c.key_a].text}' and '{aliases[c.key_b].text}' "
                        "refer to the exact same entity? "
                        f"First appears in: {contexts.get(c.key_a, '')} "
                        f"Second appears in: {contexts.get(c.key_b, '')}"
                    ),
                )
                for index, c in enumerate(batch)
            ]
            result = client.ask(ALIAS_INPUT, questions, "Knowledge graph alias check")
            asked += len(questions)
            for index, candidate in enumerate(batch):
                answer = result.answers.get(f"pair_{index}")
                if answer is None or answer.probability is None:
                    continue
                same = answer.probability >= ALIAS_MERGE_THRESHOLD
                AliasDecision.objects.update_or_create(
                    user=user,
                    key_a=candidate.key_a,
                    key_b=candidate.key_b,
                    defaults={
                        "same": same,
                        "source": AliasSource.DECISIONS,
                        "probability": answer.probability,
                    },
                )
                if same:
                    merges += int(
                        cls._merge_keys(
                            user,
                            candidate.key_a,
                            candidate.key_b,
                            AliasSource.DECISIONS,
                            answer.probability,
                        )
                    )
        return asked, merges

    @classmethod
    def _contexts(cls, user, file_ids, keys: Iterable[str]) -> Dict[str, str]:
        found: Dict[str, str] = {}
        rows = (
            cls._user_entities(user)
            .filter(file_id__in=file_ids, key__in=set(keys))
            .select_related("chunk")
            .order_by("-mentions")
        )
        for row in rows:
            if row.key in found:
                continue
            text = row.chunk.text
            position = max(text.lower().find(row.text.lower()), 0)
            start = max(position - CONTEXT_CHARS // 2, 0)
            found[row.key] = " ".join(text[start : start + CONTEXT_CHARS].split())
        return found

    @classmethod
    def _merge_keys(
        cls, user, key_a: str, key_b: str, source: str, probability: Optional[float]
    ) -> bool:
        aliases = {
            alias.key: alias
            for alias in ClusterAlias.objects.filter(user=user, key__in=[key_a, key_b])
        }
        first, second = aliases.get(key_a), aliases.get(key_b)
        if first is None or second is None or first.cluster_id == second.cluster_id:
            return False
        keep, drop = sorted(
            [first.cluster, second.cluster],
            key=lambda c: (-c.aliases.count(), c.pk),
        )
        cls._absorb(keep, drop, source, probability)
        return True

    @staticmethod
    @transaction.atomic
    def _absorb(keep, drop, source, probability) -> None:
        moved = ClusterAlias.objects.filter(cluster=drop)
        moved.update(cluster=keep, source=source, probability=probability)
        for field in ("subject", "object"):
            for relation in EntityRelation.objects.filter(**{field: drop}):
                setattr(relation, field, keep)
                duplicate = EntityRelation.objects.filter(
                    chunk=relation.chunk,
                    subject=relation.subject,
                    object=relation.object,
                    label=relation.label,
                ).exclude(pk=relation.pk)
                if duplicate.exists() or relation.subject_id == relation.object_id:
                    relation.delete()
                else:
                    relation.save(update_fields=[field])
        drop.delete()

    @classmethod
    @transaction.atomic
    def merge(cls, user, cluster: EntityCluster, other: EntityCluster) -> EntityCluster:
        """The user says these are one thing; remember it for every key pair."""
        for key_a in cluster.aliases.values_list("key", flat=True):
            for key_b in other.aliases.values_list("key", flat=True):
                low, high = _ordered(key_a, key_b)
                AliasDecision.objects.update_or_create(
                    user=user,
                    key_a=low,
                    key_b=high,
                    defaults={
                        "same": True,
                        "source": AliasSource.MANUAL,
                        "probability": None,
                    },
                )
        cls._absorb(cluster, other, AliasSource.MANUAL, None)
        cls.refresh_labels(user)
        return cluster

    @classmethod
    @transaction.atomic
    def separate(cls, user, cluster: EntityCluster, key: str) -> EntityCluster:
        """Take one spelling out into its own cluster, and never merge it back."""
        alias = cluster.aliases.get(key=key)
        for other in cluster.aliases.exclude(key=key).values_list("key", flat=True):
            low, high = _ordered(key, other)
            AliasDecision.objects.update_or_create(
                user=user,
                key_a=low,
                key_b=high,
                defaults={
                    "same": False,
                    "source": AliasSource.MANUAL,
                    "probability": None,
                },
            )
        fresh = EntityCluster.objects.create(
            user=user, label=alias.text, kind=cluster.kind
        )
        alias.cluster = fresh
        alias.source = AliasSource.MANUAL
        alias.probability = None
        alias.save(update_fields=["cluster", "source", "probability", "updated_at"])
        cls.refresh_labels(user)
        return fresh
