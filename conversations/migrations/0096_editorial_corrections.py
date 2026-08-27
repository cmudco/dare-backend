# When an alias set matches MULTIPLE rows in an environment (e.g. a base identifier
# and its dated twin, gpt-5.4 + gpt-5.4-2026-03-05), the write applies to all of them
#
# Corrects is_reasoning on a few rows, sets reasoning_level for existing models
# Future seed migrations should set reasoning_level
#
# Guarantees: never creates rows; avoids writing identifier, is_active, or token rates.
# Idempotent: absent rows are skipped with a logged notice.

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def dd(*forms):
    """Expand each form into its dot and dash spellings (order-preserving, deduped)."""
    out = []
    for f in forms:
        for v in (f, f.replace(".", "-")):
            if v not in out:
                out.append(v)
    return tuple(out)


# --- reasoning_level buckets: alias set -> level ---------------------------------
COST_UNCONSTRAINED = [
    dd("claude-fable-5"),
    dd("gpt-5"),
    dd("gpt-5-mini"),
    dd("o1"),
    dd("o3-mini"),
    dd("gemini-3.1-pro-preview"),
    dd("gemini-2.5-pro"),
    dd("gemini-3.5-flash"),
]
COST_PREDICTABLE = [
    dd("claude-sonnet-5"),
    dd("claude-sonnet-4.6"),
    dd("claude-opus-4.6"),
    dd("claude-opus-4.7"),
    dd("claude-opus-4.8"),
    dd("claude-opus-5"),
    dd("gpt-5.2", "gpt-5.2-2025-12-11"),
    dd("gpt-5.6-terra"),
    dd("gpt-5.6-luna"),
    dd("gpt-5.6-sol"),
    dd("gemini-3.1-flash-lite"),
    dd("gemini-3.1-flash-lite-preview"),
    dd("gemini-3.5-flash-lite"),
    dd("gemini-3.7-flash"),
]

IS_REASONING_SEVEN = [
    dd("claude-opus-4.7"),
    dd("claude-opus-4.8"),
    dd("claude-sonnet-4.6"),
    dd("gemini-3.1-pro-preview"),
    dd("gemini-3.1-flash-lite"),
    dd("gemini-3.1-flash-lite-preview"),
    dd("gemini-3.5-flash"),
]

# --- tier: (alias set, expected current 'from', new tier). Write only on exact from.
TIER_MOVES = [
    (dd("claude-opus-4-5-20251101", "claude-opus-4.5"), "advanced", "premium"),
    (dd("claude-opus-4.6"), "advanced", "premium"),
    (dd("claude-haiku-4-5-20251001", "claude-haiku-4.5"), "advanced", "flash"),
]

# --- alias sets needed to resolve description slugs ----------------------
DESC_EXTRA_ALIASES = [
    dd("gpt-5.4", "gpt-5.4-2026-03-05"),
    dd("gpt-5.5", "gpt-5.5-2026-04-23"),
    dd("gpt-5.1", "gpt-5.1-2025-11-13"),
    dd("gpt-5.4-mini"),
    ("dall-e-2",),
    ("dall-e-3",),
]

DESCRIPTIONS = [
    ("claude-opus-4-5-20251101", "Premium model combining high intelligence with practical performance. No adaptive thinking."),
    ("claude-opus-4-6-premium", "Improved long-context management; sustained work over large documents and codebases."),
    ("claude-opus-4-7", "Strong agentic coding and good for long-horizon tasks."),
    ("claude-opus-4-8", "Adaptive reasoning, adds 1M context and improved coding reliability."),
    ("claude-opus-5", "Thinking on by default; succeeds 4.8."),
    ("gpt-5-4", "Uses fewer tokens than earlier models; 1M context; strong computer use."),
    ("gpt-5-5", "Succeeds 5.4; needs less guidance on multi-step, long horizon work."),
    ("claude-sonnet-4-6", "Balanced Anthropic model; adds adaptive thinking and effort controls."),
    ("dall-e-2", "OpenAI image generation model (earlier generation)."),
    ("dall-e-3", "OpenAI image generation model; higher fidelity and prompt adherence than DALL-E 2."),
    ("gpt-5-1", "OpenAI reasoning model with configurable thinking effort."),
    ("gpt-5-2-premium", "Succeeds 5.1; positioned for complex professional work; general-purpose, standard thinking."),
    ("gpt-5", "Routes each prompt to fast or thinking mode automatically; multimodal; first unified GPT model."),
    ("gemini-3-1-pro-preview", "Google deep reasoning pro tier model; strong multimodal and long-context; preview."),
    ("gpt-5-mini-cost-efficient", "Compact GPT-5 reasoning variant; lower per-token cost."),
    ("gpt-5-4-mini", "Compact GPT-5.4 reasoning variant at lower cost."),
    ("gemini-3-1-flash-lite-preview", "Preview of Google's cost-efficient model; thinking levels for high-volume use."),
    ("gemini-3-1-flash-lite", "Google cost-efficient model; low latency and thinking levels for high-volume tasks."),
    ("gemini-3-5-flash", "Beats 3.1 Pro on agentic and coding tasks at Flash speed and cost."),
    ("gemini-3-5-flash-lite", "Low-cost, high-volume tasks; minimal thinking by default."),
    ("gemini-3-7-flash", "Agentic Gemini workhorse; refines 3.6 Flash; always reasons at least briefly."),
    ("claude-fable-5", "Anthropic Mythos-class model; always-on adaptive reasoning for the hardest problems."),
    ("claude-sonnet-5", "Succeeds 4.6; thinking on by default, new tokenizer."),
    ("gpt-5-6-terra", "Middle GPT-5.6 variant balancing capability and cost."),
    ("gpt-5-6-luna", "Smallest GPT-5.6 variant; lowest cost and latency."),
    ("gpt-5-6-sol", "Largest GPT-5.6 variant; pro reasoning mode with higher latency."),
]

_desc_slugs = [slug for slug, _ in DESCRIPTIONS]
assert len(_desc_slugs) == len(set(_desc_slugs)), "duplicate DESCRIPTIONS slugs"

DESC_TIER_SUFFIXES = ("-premium", "-cost-optimized", "-cost-efficient")


def forward(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    ModelCardData = apps.get_model("conversations", "ModelCardData")

    changed = 0  # rows whose value actually differed (proves idempotency on re-run)

    def match_rows(aliases):
        """All LLM rows whose identifier is in the alias set (dated twins included)."""
        return list(LLM.objects.filter(identifier__in=list(aliases)))

    def note_multi(label, rows):
        if len(rows) > 1:
            logger.info(
                "0096: alias set %s matched %d rows %s — applying to all",
                label, len(rows), [r.identifier for r in rows],
            )

    def set_llm(rows, field, value):
        nonlocal changed
        if not rows:
            return 0
        n = (
            LLM.objects.filter(pk__in=[r.pk for r in rows])
            .exclude(**{field: value})
            .update(**{field: value})
        )
        changed += n
        return n

    def set_card_reasoning(rows, value):
        nonlocal changed
        if not rows:
            return
        changed += (
            ModelCardData.objects.filter(llm_id__in=[r.pk for r in rows])
            .exclude(reasoning_level=value)
            .update(reasoning_level=value)
        )

    # 1. reasoning_level on classified rows (+ linked cards) --------------------
    r_matched = r_absent = 0
    for level, sets in (
        ("cost_unconstrained", COST_UNCONSTRAINED),
        ("cost_predictable", COST_PREDICTABLE),
    ):
        for aliases in sets:
            rows = match_rows(aliases)
            if not rows:
                logger.info("0096 reasoning_level[%s]: %s absent — skipped", level, aliases[0])
                r_absent += 1
                continue
            note_multi(aliases, rows)
            set_llm(rows, "reasoning_level", level)
            set_card_reasoning(rows, level)
            r_matched += 1

    # 2. is_reasoning = True -------------------------------
    ir_matched = ir_absent = 0
    for aliases in IS_REASONING_SEVEN:
        rows = match_rows(aliases)
        if not rows:
            logger.info("0096 is_reasoning: %s absent — skipped", aliases[0])
            ir_absent += 1
            continue
        note_multi(aliases, rows)
        set_llm(rows, "is_reasoning", True)
        ir_matched += 1

    # 3. tier moves, guarded on exact 'from' (per matched row) ------------------
    t_applied = t_absent = t_wrongfrom = 0
    for aliases, expect_from, new_tier in TIER_MOVES:
        rows = match_rows(aliases)
        if not rows:
            logger.info("0096 tier: %s absent — skipped", aliases[0])
            t_absent += 1
            continue
        note_multi(aliases, rows)
        for row in rows:
            if row.tier != expect_from:
                logger.info(
                    "0096 tier: %s current=%r != expected-from=%r — skipped",
                    row.identifier, row.tier, expect_from,
                )
                t_wrongfrom += 1
                continue
            set_llm([row], "tier", new_tier)
            t_applied += 1

    # 4. descriptions --------------------------------------
    alias_to_rows = {}
    for aliases in (
        COST_UNCONSTRAINED + COST_PREDICTABLE
        + IS_REASONING_SEVEN + [t[0] for t in TIER_MOVES] + DESC_EXTRA_ALIASES
    ):
        rows = match_rows(aliases)
        for spelling in aliases:
            alias_to_rows.setdefault(spelling, rows)

    def resolve_slug(slug):
        rows = alias_to_rows.get(slug)
        if rows:
            return rows
        for suffix in DESC_TIER_SUFFIXES:
            if slug.endswith(suffix):
                return alias_to_rows.get(slug[: -len(suffix)]) or []
        return []

    d_resolved = d_unresolved = 0
    unresolved = []
    for slug, description in DESCRIPTIONS:
        rows = resolve_slug(slug)
        if not rows:
            unresolved.append(slug)
            d_unresolved += 1
            continue
        note_multi((slug,), rows)
        set_llm(rows, "description", description)
        d_resolved += 1
    if unresolved:
        logger.info("0096 descriptions: %d slug(s) unresolved (base model absent), skipped: %s",
                    d_unresolved, unresolved)

    logger.info(
        "0096 summary | reasoning matched=%d absent=%d (=%d) | "
        "is_reasoning matched=%d absent=%d | tier applied=%d absent=%d wrong-from=%d | "
        "descriptions resolved=%d unresolved=%d | rows changed this pass=%d",
        r_matched, r_absent, r_matched + r_absent,
        ir_matched, ir_absent, t_applied, t_absent, t_wrongfrom,
        d_resolved, d_unresolved, changed,
    )


def reverse(apps, schema_editor):
    """Noop: corrections are re-applied forward, not restored."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0095_seed_gemini_3_7_flash"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
