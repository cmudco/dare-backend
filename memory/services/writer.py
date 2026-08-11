"""The memory writer — the model call, and nothing else.

It runs after a reply has finished streaming, never inside the request path.
Reads outnumber writes roughly twenty to one, and that asymmetry is the whole
reason the expensive half lives out here in a background job.

What to do with what comes back lives in memory/domain/apply.py, which is pure
and tested.

The system prompt and every schema field description are ported VERBATIM from
the reference prototype's writer (writer.ts) — the evaluation scorecard showed
run-to-run quality hinges on this exact text, so any "improvement" here is an
unmeasured regression until the scorecard exists on this side. Resist editing.
"""

import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from config.env import MEMORY_WRITER_MODEL, OPENAI_API_KEY
from memory.constants import TOKEN_BUDGET, TOPICS
from memory.domain.keys import key_for, procedure_key
from memory.domain.types import MemoryRow, WriterDecision
from memory.domain.user_doc import (PROFILE_HEADINGS, estimate_tokens,
                                    user_doc_lines)

logger = logging.getLogger(__name__)

_PROFILE_KEY_GLOSS = ", ".join(
    f"{key} ({heading})" for key, heading in PROFILE_HEADINGS.items()
)

ActionLiteral = Literal[
    "patch_user", "add_fact", "add_procedure", "supersede", "ignore"
]
ProfileKeyLiteral = Literal[
    "identity",
    "background",
    "communication",
    "working-preferences",
    "constraints",
    "boundaries",
]
TopicLiteral = Literal[
    "name",
    "style",
    "diet",
    "diet_avoid",
    "schedule",
    "location",
    "occupation",
    "industry",
    "habit",
    "health",
    "person",
    "project",
    "note",
]
SensitivityLiteral = Literal["none", "health", "safety", "third-party"]


class Decision(BaseModel):
    action: ActionLiteral
    reason: str = Field(
        description=(
            "One short sentence explaining this choice, written for a person "
            "reading an audit log. Required even for ignore — especially for "
            "ignore."
        )
    )
    # Required, not nullable. Left optional the model returns patch_user with
    # no heading about half the time, and a write that should have landed is
    # dropped. Other actions ignore the value, so forcing a choice costs
    # nothing.
    profile_key: ProfileKeyLiteral = Field(
        description=(
            f"For patch_user: the USER.md heading this line goes under, as a "
            f"key — one of {_PROFILE_KEY_GLOSS}. identity is what to call them "
            f"and where they are; background is durable history; communication "
            f"is how they want answers written; working-preferences is how "
            f"they like to work; constraints are hard limits, including "
            f"allergies; boundaries are rules about what may be remembered. "
            f"Only read for patch_user — pick the closest fit for anything "
            f"else."
        )
    )
    text: Optional[str] = Field(
        description=(
            "patch_user: the bullet, one short sentence. add_fact: a short "
            "third-person statement. add_procedure: the rule alone, as an "
            "imperative, with the trigger left out of it — 'use pnpm', not "
            "'when installing packages, use pnpm'. supersede: the new "
            "statement replacing the old one."
        )
    )
    trigger: Optional[str] = Field(
        description=(
            "REQUIRED for add_procedure, ignored otherwise. WHEN the rule "
            "applies, as two or three plain words naming the situation: "
            "'writing commit messages', 'installing packages', 'reviewing my "
            "code', 'writing sql'. Never put the rule itself here. One "
            "situation can hold several rules, so emit one decision per rule "
            "rather than combining them into a sentence — 'no emoji' and "
            "'subject under 50 characters' are two procedures that share a "
            "trigger, not one."
        )
    )
    topic: Optional[TopicLiteral] = Field(
        description=(
            "What this is about. Required for add_fact and supersede. Fill it "
            "in for patch_user too whenever the statement has a natural topic, "
            "because a profile line that is refused becomes a fact and needs "
            "somewhere to live.\n"
            "The wrong choice retires a fact that never changed.\n"
            "name: the one thing they want to be called. style: an aspect of "
            "how they want answers written, with which aspect in the qualifier "
            "('length', 'format', 'tone'). diet: their one overall eating "
            "pattern. diet_avoid: a specific thing they will not eat, that "
            "thing in the qualifier; an allergy belongs here with sensitivity "
            '"safety". schedule: a standing availability rule, never a one-off '
            "meeting or deadline. location: the one place they live — never a "
            "place they merely asked about, and the statement itself MUST name "
            "that place. If the sentence mentions a city but the thing worth "
            "keeping is not where they live, the topic is not location: \"I'm "
            'in London, so everything in GMT" is a location fact AND a '
            "separate preference about time zones, and filing the preference "
            "under location loses the city and later gets it deleted by the "
            "next thing misfiled there. occupation: their one current job. "
            "industry: their one sector; never mix the two. habit: something "
            "the conversation shows actually happening, with WHAT it is in the "
            "qualifier; an intention is not a habit and a preference is not a "
            "habit. person: another named individual. health: anything "
            "medical, with what it concerns in the qualifier. project: a named "
            "piece of ongoing work. note: any durable fact that fits nothing "
            "above — an account, a certificate, a reference number, a document "
            "format. Put what it is ABOUT in the qualifier, because two notes "
            "are almost never the same fact."
        )
    )
    qualifier: Optional[str] = Field(
        description=(
            "REQUIRED for person, health, habit, project, schedule, "
            "diet_avoid, note and style. Empty for name, diet, location, "
            "occupation and industry, where only one thing can be true at a "
            "time. It is what distinguishes two facts under the same topic: "
            "the person's name ('sam-okafor' vs 'sam-sister'), what a health "
            "fact concerns ('peanut' vs 'ankle'), WHAT a habit is rather than "
            "when it happens ('gym', 'deep-work' — never 'monday'), what a "
            "note is ABOUT ('ubl-account', 'pseb-certificate'), and which "
            "aspect of style ('length', 'format', 'tone'). Two facts sharing "
            "a topic AND qualifier are treated as the same fact, and the "
            "newer one RETIRES the older. Unrelated things must therefore "
            "never share a qualifier — leaving it empty on these topics "
            "deletes an unrelated memory."
        )
    )
    importance: Optional[float] = Field(
        description=(
            "0 to 1. How much worse is an answer that does not know this? A "
            "severe allergy is 1.0. A preferred answer length is 0.6. "
            "Yesterday's weather is 0.0. Retrieval will rank on this, so a "
            "flat 0.5 for everything makes the ranking useless."
        )
    )
    confidence: Optional[float] = Field(
        description="0 to 1. Stated outright is around 0.95; inferred is lower."
    )
    sensitivity: Optional[SensitivityLiteral] = Field(
        description=(
            "The test is ONE question: if you never mentioned this, could you "
            "end up suggesting something that hurts them or wastes their "
            "time?\n\n"
            "'safety' — yes. Allergies, intolerances, contraindications, and "
            "any medical fact that CONSTRAINS WHAT YOU MAY SUGGEST. \"I'm on "
            'crutches for six weeks" is safety: propose a walk across town in '
            "ignorance of it and the suggestion is useless at best. A "
            'temporary injury counts; so does anything they follow with "so '
            "don't suggest…\". Safety is retrieved and acted on.\n\n"
            "'health' — no. A condition, a symptom, a medication, mentioned "
            "in passing while telling you about their day, where saying "
            'nothing costs nothing. "I\'ve been getting migraines lately" is '
            "health. It is held back and never surfaces in an answer.\n\n"
            "Do not reach for 'health' because a fact feels private. Reach "
            "for it because staying silent about it is free. When a medical "
            "fact would change what you recommend, it is 'safety'."
        )
    )
    occurred_at: Optional[str] = Field(
        description=(
            "YYYY-MM-DD, only when the message says when something happened "
            "or becomes true."
        )
    )
    valid_until: Optional[str] = Field(
        description=(
            "YYYY-MM-DD, only when the message says when this STOPS being "
            'true. "On crutches for the next six weeks", "contract runs until '
            'March", "visiting for ten days" — work the date out from TODAY. '
            "Leave null for anything open-ended. This is what stops a "
            "temporary fact being believed forever: a healed ankle that never "
            "expires is a system that keeps refusing to suggest a walk."
        )
    )
    supersedes_id: Optional[str] = Field(
        description=(
            "For supersede only. The exact id of the existing memory that is "
            "no longer current."
        )
    )
    replaces_line: Optional[str] = Field(
        description=(
            "For patch_user only, and only when USER.md is near its budget. "
            "The existing bullet, copied exactly, that this line should "
            "replace."
        )
    )


class WriterResponse(BaseModel):
    decisions: List[Decision] = Field(
        description=(
            "One entry per thing worth deciding about — one MESSAGE often "
            "contains several. Split them: \"I'm in London, so everything in "
            'GMT please" is two facts, and "I\'m vegetarian and I hate long '
            'answers" is two. Never merge two facts into one entry to save '
            "space; a merged entry gets one key, and the half that does not "
            "match that key is lost. Never return an empty array — return a "
            "single ignore instead."
        )
    )


SYSTEM = f"""You maintain the long-term memory of one person, from their conversations with an assistant.

You are looking at ONE completed turn. Decide what, if anything, should be written down. There are two places something can go, and two ways to say no.

You do NOT need to record what happened. The full transcript of every conversation is kept verbatim and is searchable, so a summary of an event would only be a lossy copy of something already held perfectly. Record what is TRUE, not what occurred.

USER.md — the sticky document, injected into every single conversation.
  Use "patch_user" ONLY for what shapes how you should TALK to this person: what to call them, how they want answers written, a hard constraint you could hurt them by ignoring, a rule about what may be remembered. It is capped at {TOKEN_BUDGET} tokens, so a line has to earn permanent residence in every future prompt.
  These are NOT profile lines, no matter how durable they are — they are facts: where someone lives, what they do for work, who they know, what they are working on, an account, a certificate, a date. If it has a natural topic below, it is a fact.
  One turn is weak evidence that something is stable. If you are unsure a preference will still hold next month, it is a fact, not a profile line.

Facts — the searchable archive, read only when a question needs them.
  Use "add_fact" for durable specifics: where they live, what they do, a named person, a project, an allergy, a standing schedule rule. Most of what is worth keeping is a fact.

Procedures — rules about HOW to do a thing, fetched when that thing is about to happen.
  Use "add_procedure" for a standing instruction with a situation attached: a correction they gave you, a convention they want followed, a tool or approach they want used or avoided. "Always run the tests before you say you are done." "Never use emoji in commit messages." "When I share SQL, check the joins first."
  The test that separates a procedure from a fact: a fact answers a QUESTION, a procedure fires during a TASK. "I use pnpm" is a fact — it answers "what package manager do I use". "When installing packages, use pnpm and never npm" is a procedure, because the turn where it matters will not mention package managers at all.
  The test that separates a procedure from a profile line: a profile line is about how to TALK to them and applies to every turn. A procedure applies only in its situation. "Keep answers short" is a profile line. "When reviewing my code, be blunt" is a procedure.
  Prefer a procedure whenever the person is correcting you or telling you how they want something done.

Saying no.
  "ignore" for small talk, weather, how they feel right now, the assistant's own output, and anything already recorded.
  The test is whether it will still be TRUE in a month, not whether it seems important. "I have an iPhone 15 Pro Max" is a small fact and a durable one: record it with a low importance. Judging something unimportant is what the importance score is for — a 0.2 fact costs almost nothing and ranks near the bottom, whereas ignoring it loses it for good. When a statement is durable but minor, record it and score it low.
  "supersede" when the person says something that makes an EXISTING memory no longer true — they moved, changed jobs, dropped a preference. Give the exact id from ALREADY KNOWN. Retiring is destructive, so never guess: if something is merely different rather than contradictory, add a new fact instead.

Rules that keep the store honest:
- Record what things are; do not decide what to do about them.
- EVERY statement must stand on its own, because it REPLACES whatever shared its key and nothing else survives to complete it. Write the whole fact, never the change: "lives in Pittsburgh", not "is no longer in Boston"; "not available before 10am", not "not available early"; "works in healthcare", not "changed industry". A statement that only says what is no longer true destroys the one that said what IS true, and leaves the store unable to answer the question at all. If you cannot state the new fact completely, say ignore.
- Keep the specifics. A number, a city, a day, a time is usually the entire value of the fact — "before 10am" is the fact; "early" is not.
- An INTENTION is not a fact. "I need to find a new gym", "I should start running", "I'm going to look into that" describe a plan, not something that is true. Ignore them. Filing one as a habit is worse than losing it: "needs a new gym" and "goes to the gym on Mondays" collide on the same key, and the intention retires the habit.
- Any durable statement the person makes about themselves, their things, their people or their work is a fact, however ordinary. Devices they own, accounts they hold, tools they use, documents they have — all facts.
- If the person corrects themselves mid-message, record only the correction.
- Never write down something the assistant said unless the person confirmed it.
- Telling you not to remember something means store nothing. It is not an instruction to delete anything.
- Do not restate something already in USER.md or ALREADY KNOWN. Say ignore, and say that is why.
- Two facts sharing a topic and qualifier are the same fact, and the newer retires the older. Qualify anything that can be true twice over.
- Choosing a topic is choosing what this fact will DELETE later. An unqualified topic — name, diet, location, occupation, industry — holds exactly one fact, so filing something under the wrong one silently destroys the right one the next time that topic is used. If a statement is not squarely about the topic, use "note" with a qualifier instead. "note" deletes nothing it should not.
- When in doubt between a profile line and a fact, choose the fact. A fact can be promoted later; a wrong profile line costs tokens on every future turn and has no topic to collide with, so it can never be corrected by a supersede."""

assert set(TOPICS) == set(
    TopicLiteral.__args__
), "TopicLiteral must mirror memory.constants.TOPICS"


def _is_malformed(decision: "Decision") -> bool:
    """A non-ignore decision with no statement cannot be applied — the gate
    will refuse it, and refusing a safety fact loses it. Caught in a live
    E2E: gpt-4o returned a perfect allergy decision (topic, sensitivity,
    importance 1.0) with ``text: null``."""
    return decision.action != "ignore" and not (decision.text or "").strip()


_REPAIR_NOTE = (
    "Your previous response included a decision whose `text` field was null "
    "or empty. Every decision except `ignore` MUST carry its complete "
    "standalone statement in `text` — a decision without one cannot be "
    "stored and the fact is lost. Re-emit the full set of decisions with "
    "every `text` filled in."
)


def propose_decisions(
    user_doc: str,
    archive: List[MemoryRow],
    user_message: str,
    assistant_message: str,
    explicit: bool,
    now: Optional[str] = None,
    model: Optional[str] = None,
) -> List[WriterDecision]:
    """Ask the model what to do. Nothing is written here.

    One in-job repair retry when a decision arrives with an empty statement.
    This cannot violate the queue's ordering guarantee — nothing has been
    persisted yet; it is the same turn asking its question twice.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)
    model = model or MEMORY_WRITER_MODEL
    moment = now or datetime.now(timezone.utc).isoformat()

    # What the retriever thought was relevant to this turn — not the most
    # recent forty, which at a thousand memories is a window onto last week.
    # The writer needs to see what it might be repeating or contradicting.
    known = [
        f"{row.id} · {row.kind}{f' · {row.key}' if row.key else ''} · {row.text}"
        for row in archive
        if row.state == "active"
    ]

    lines = user_doc_lines(user_doc)
    tokens = estimate_tokens(user_doc)
    near_limit = tokens >= TOKEN_BUDGET * 0.8

    doc_block = (
        "\n".join(f"[{item['key']}] {item['line']}" for item in lines)
        if lines
        else "(empty)"
    )
    known_block = "\n".join(known) if known else "(nothing yet)"
    explicit_block = (
        "\n\nThe person explicitly asked for something to be remembered. That "
        "is consent in their own words, so a profile line is allowed here if "
        "the content genuinely belongs in USER.md."
        if explicit
        else ""
    )

    prompt = f"""TODAY: {moment[:10]}

USER.md — {tokens} of {TOKEN_BUDGET} tokens used{
        " (near the ceiling: a new line must replace an existing one)" if near_limit else ""
    }
{doc_block}

ALREADY KNOWN — the memories most related to this turn. Do not record any of them again.
{known_block}

THE TURN
PERSON: {user_message}
ASSISTANT: {assistant_message or "(no reply captured)"}{explicit_block}"""

    def ask(messages) -> Optional[WriterResponse]:
        completion = client.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=WriterResponse,
            temperature=0,
            max_tokens=900,
        )
        return completion.choices[0].message.parsed

    base_messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]
    parsed = ask(base_messages)
    if parsed is not None and any(_is_malformed(d) for d in parsed.decisions):
        logger.warning(
            "[memory] writer emitted a decision with empty text; retrying once"
        )
        retried = ask(base_messages + [{"role": "user", "content": _REPAIR_NOTE}])
        if retried is not None and not any(_is_malformed(d) for d in retried.decisions):
            parsed = retried
        # Otherwise keep the original: the gate refuses the malformed halves
        # with a ledger entry, which is at least visible.

    if parsed is None:
        logger.warning("[memory] writer returned no parseable decisions")
        return []

    decisions: List[WriterDecision] = []
    for decision in parsed.decisions:
        # One routing field. A profile line is keyed by its heading; a
        # procedure by WHEN it fires; a fact by its qualified topic. All
        # answer the same question — where does this go, and what does it
        # collide with.
        if decision.action == "patch_user":
            key = decision.profile_key
        elif decision.action == "add_procedure":
            key = procedure_key(decision.trigger, decision.text)
        elif decision.topic:
            key = key_for(decision.topic, decision.qualifier, decision.text)
        else:
            key = None

        decisions.append(
            WriterDecision(
                action=decision.action,
                reason=decision.reason,
                text=decision.text,
                key=key,
                # Read only on the patch_user→add_fact downgrade, so a refused
                # profile line still collides with the same fact stated later.
                topic_key=(
                    key_for(decision.topic, decision.qualifier, decision.text)
                    if decision.topic
                    else None
                ),
                trigger=decision.trigger,
                importance=decision.importance,
                confidence=decision.confidence,
                sensitivity=decision.sensitivity,
                occurred_at=decision.occurred_at,
                valid_until=decision.valid_until,
                supersedes_id=decision.supersedes_id,
                replaces_line=decision.replaces_line,
            )
        )
    return decisions
