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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Literal, Optional

from asgiref.sync import async_to_sync
from pydantic import BaseModel, Field

from config.env import MEMORY_WRITER_MODEL
from conversations.models import LLM
from core.services.api_key_service import get_provider_api_key_sync
from core.services.billing_service import BillingService
from core.services.openai_service import OpenAIService
from memory.constants import TOKEN_BUDGET, TOPICS, Sensitivity
from memory.domain.keys import key_for, procedure_key
from memory.domain.types import MemoryRow, WriterDecision
from memory.domain.user_doc import PROFILE_HEADINGS, estimate_tokens, user_doc_lines

logger = logging.getLogger(__name__)

_PROFILE_KEY_GLOSS = ", ".join(
    f"{key} ({heading})" for key, heading in PROFILE_HEADINGS.items()
)

ActionLiteral = Literal["add_fact", "add_procedure", "supersede", "ignore"]
ProfileKeyLiteral = Literal[*tuple(PROFILE_HEADINGS)]
TopicLiteral = Literal[*TOPICS]
SensitivityLiteral = Literal[*tuple(Sensitivity.values)]


class Decision(BaseModel):
    action: ActionLiteral
    reason: str = Field(
        description=(
            "One short sentence explaining this choice, written for a person "
            "reading an audit log. Required even for ignore — especially for "
            "ignore."
        )
    )
    pinned_to: Optional[ProfileKeyLiteral] = Field(
        description=(
            f"The USER.md heading when this fact must travel into every future "
            f"conversation, otherwise null. Choose from {_PROFILE_KEY_GLOSS}. "
            f"identity is what to call them "
            f"and where they are; background is durable history; communication "
            f"is how they want answers written; working-preferences is how "
            f"they like to work; constraints are hard limits, including "
            f"allergies; boundaries are rules about what may be remembered. "
            "Use null for everything they "
            "merely told you, however interesting: a fact is found when it is "
            "needed and costs nothing in between. Judge the content, not "
            "whether they asked — permission is decided separately."
        )
    )
    text: Optional[str] = Field(
        description=(
            'add_fact: a short third-person statement — write "the person" '
            'or "they", never '
            "their name. Every memory in this store is already about them, so "
            "the name adds nothing and costs twice: it makes every fact match "
            "any message that says their name, and it goes stale the day they "
            "ask to be called something else. add_procedure: the rule alone, "
            "as an "
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
    applies_when: Optional[str] = Field(
        description=(
            "REQUIRED for add_procedure, ignored otherwise. One sentence "
            "naming the situations this rule fires in, written the way those "
            "moments actually arrive: 'Reviewing code they share — a "
            "function, a diff, a pull request, a snippet they want feedback "
            "on.' The rule is FOUND by this sentence, not by its wording, and "
            "the turn it applies to will rarely use the same words the rule "
            "does — someone says 'take a look at this' and means review my "
            "code. Name the artefacts and the phrasings, not the lesson."
        )
    )
    topic: Optional[TopicLiteral] = Field(
        description=(
            "What this is about. Required for add_fact and supersede. The "
            "wrong choice retires a fact that never changed.\n"
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
            "piece of ongoing work. boundaries: a standing rule about what "
            "memory may store, with its subject in the qualifier. note: any "
            "durable fact that fits nothing "
            "above — an account, a certificate, a reference number, a document "
            "format. Put what it is ABOUT in the qualifier, because two notes "
            "are almost never the same fact."
        )
    )
    qualifier: Optional[str] = Field(
        description=(
            "REQUIRED for person, health, habit, project, schedule, "
            "diet_avoid, boundaries, note and style. Empty for name, diet, location, "
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
    is_snapshot: bool = Field(
        description=(
            "Is this a MEASURED VALUE that will be a different number later? "
            "A portfolio balance, a follower count, a weight, a salary, a "
            "step count, an age. True for those. False for facts that stay "
            "put until something replaces them — where they live, what they "
            "do, what they are allergic to.\n"
            'When true, write the date INTO the statement: "Their portfolio '
            'was 8M in August 2026", never "their portfolio is 8M". '
            "Today's date is at the top of this prompt. Said without a date, "
            "a measurement is read a year later as though it were still true, "
            "and nothing in the store can tell that it went stale."
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
    reinforces_id: Optional[str] = Field(
        description=(
            "For ignore only, and only when the reason for ignoring is that "
            "the person restated something ALREADY KNOWN. The exact id of the "
            "memory they repeated. Repetition is the only evidence that a "
            "memory still matters, so saying which one was repeated is worth "
            "more than the ignore itself."
        )
    )


class WriterResponse(BaseModel):
    explicit_request: bool = Field(
        description=(
            "Did the PERSON ask for something to be remembered in this "
            "message? True for any wording that asks you to keep something — "
            '"remember that…", "keep this in mind", "note this down", "always '
            'call me…", "don\'t forget…". False when they merely stated '
            "something, however important it sounds. This is about what THEY "
            "asked for, never about whether you judge it worth keeping: a "
            "fact you consider valuable is still False if they did not ask. "
            "It decides whether a line may be written into the file that is "
            "read on every future turn, so guessing yes is expensive."
        )
    )
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
  Set `pinned_to` ONLY for what shapes how you should TALK to this person: what to call them, how they want answers written, a hard constraint you could hurt them by ignoring, a rule about what may be remembered. It is capped at {TOKEN_BUDGET} tokens, so a line has to earn permanent residence in every future prompt.
  These are NOT profile lines, no matter how durable they are — they are facts: where someone lives, what they do for work, who they know, what they are working on, an account, a certificate, a date. If it has a natural topic below, it is a fact.
  One turn is weak evidence that something is stable. If you are unsure a preference will still hold next month, it is a fact, not a profile line.

Facts — the searchable archive, read only when a question needs them.
  Use "add_fact" for durable specifics: where they live, what they do, a named person, a project, an allergy, a standing schedule rule. Most of what is worth keeping is a fact.

Procedures — rules about HOW to do a thing, fetched when that thing is about to happen.
  Use "add_procedure" for a standing instruction with a situation attached: a correction they gave you, a convention they want followed, a tool or approach they want used or avoided. "Always run the tests before you say you are done." "Never use emoji in commit messages." "When I share SQL, check the joins first."
  The test that separates a procedure from a fact: a fact answers a QUESTION, a procedure fires during a TASK. "I use pnpm" is a fact — it answers "what package manager do I use". "When installing packages, use pnpm and never npm" is a procedure, because the turn where it matters will not mention package managers at all.
  One sentence is often BOTH, and then it needs BOTH decisions. "We use pnpm at work, npm breaks our lockfile" states what they use AND tells you what to do — emit the fact and the procedure. Choosing only the procedure is the more damaging half to lose: rules are fetched by the task and never by a question, so the fact becomes unanswerable and "which package manager do we use?" returns nothing.
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
- Do not restate something already in USER.md or ALREADY KNOWN. Say ignore, say that is why, and put the id of the memory they repeated in `reinforces_id`.
- Two facts sharing a topic and qualifier are the same fact, and the newer retires the older. Qualify anything that can be true twice over.
- Choosing a topic is choosing what this fact will DELETE later. An unqualified topic — name, diet, location, occupation, industry — holds exactly one fact, so filing something under the wrong one silently destroys the right one the next time that topic is used. If a statement is not squarely about the topic, use "note" with a qualifier instead. "note" deletes nothing it should not.
- When in doubt between a profile line and a fact, choose the fact. A fact can be promoted later; a wrong profile line costs tokens on every future turn and has no topic to collide with, so it can never be corrected by a supersede."""


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

WRITER_MAX_TOKENS = 4000


@dataclass
class WriterProposal:
    """What the writer came back with: the decisions, and whether the person
    asked for anything to be kept."""

    decisions: List[WriterDecision]
    explicit: bool = False


def propose_decisions(
    user,
    source_message_id: int,
    user_doc: str,
    archive: List[MemoryRow],
    user_message: str,
    assistant_message: str,
    now: Optional[str] = None,
    model: Optional[str] = None,
    keys_in_use: Optional[List[str]] = None,
) -> WriterProposal:
    """Ask the model what to do. Nothing is written here.

    One in-job repair retry when a decision arrives with an empty statement.
    This cannot violate the queue's ordering guarantee — nothing has been
    persisted yet; it is the same turn asking its question twice.
    """
    model = model or MEMORY_WRITER_MODEL
    llm = LLM.objects.get(identifier=model, is_active=True)
    service = OpenAIService(
        llm=llm,
        api_key=get_provider_api_key_sync(llm.provider),
    )
    billing = BillingService()
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

    # The keys, separately from the rows. ALREADY KNOWN is whatever retrieval
    # judged related to THIS turn, so a colliding row that scored below the
    # floor never reaches the writer and it mints a fresh key — after which
    # the two facts can never retire one another. Keys are three words each,
    # so the whole namespace fits where a dozen rows would not.
    keys_block = ", ".join(keys_in_use) if keys_in_use else "(none yet)"
    prompt = f"""TODAY: {moment[:10]}

USER.md — {tokens} of {TOKEN_BUDGET} tokens used{
        " (near the ceiling: a new line must replace an existing one)" if near_limit else ""
    }
{doc_block}

ALREADY KNOWN — the memories most related to this turn. Do not record any of them again.
{known_block}

KEYS IN USE — every slot this person's archive already has.
If this turn is about the same thing as one of these, reuse that key EXACTLY, even when the memory itself is not shown above. A key is a slot: reusing one lets the new fact replace the old, while a near-miss spelling creates a second slot and both versions survive forever with nothing to say which is current. Match on the subject, not the wording — a new phone belongs in the key the old phone is in.
{keys_block}

THE TURN
PERSON: {user_message}
ASSISTANT: {assistant_message or "(no reply captured)"}

Set `explicit_request` from what the PERSON asked for in this message, not from how useful the content looks to you. It decides whether anything may be written into USER.md, which is read on every future turn."""

    def ask(messages) -> WriterResponse:
        parsed, usage = async_to_sync(service.parse_structured_output)(
            messages=messages,
            response_model=WriterResponse,
            max_tokens=WRITER_MAX_TOKENS,
        )
        billing.record_service_usage(
            user=user,
            llm=llm,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            description=f"Memory writer for message {source_message_id}",
        )
        return parsed

    base_messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]
    parsed = ask(base_messages)
    if any(_is_malformed(d) for d in parsed.decisions):
        logger.warning(
            "[memory] writer emitted a decision with empty text; retrying once"
        )
        retried = ask(base_messages + [{"role": "user", "content": _REPAIR_NOTE}])
        if not any(_is_malformed(d) for d in retried.decisions):
            parsed = retried
        # Otherwise keep the original: the gate refuses the malformed halves
        # with a ledger entry, which is at least visible.

    decisions: List[WriterDecision] = []
    for decision in parsed.decisions:
        if decision.action == "add_procedure":
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
                applies_when=decision.applies_when,
                pinned_to=decision.pinned_to,
                importance=decision.importance,
                confidence=decision.confidence,
                sensitivity=decision.sensitivity,
                occurred_at=decision.occurred_at,
                is_snapshot=decision.is_snapshot,
                valid_until=decision.valid_until,
                supersedes_id=decision.supersedes_id,
                reinforces_id=decision.reinforces_id,
            )
        )
    return WriterProposal(decisions=decisions, explicit=parsed.explicit_request)
