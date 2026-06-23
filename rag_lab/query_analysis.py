"""
Query-analysis step (Track A, mistake #4) — turn a raw user query into a structured
retrieval plan using a FAST, CHEAP model with guaranteed-JSON structured output.

Model: claude-haiku-4-5 ($1 / $5 per 1M in/out). This is a classification+rewrite task,
exactly what Haiku is for. Output is schema-constrained (no parsing/regex on model text).

What it produces per query:
  intent          precise_lookup | exploratory | comparison   -> GATES conditional MMR
  keywords        exact terms (names, IDs, codes)              -> strengthens the BM25 leg of hybrid
  rewritten_query cleaned, disambiguated query                 -> better dense embedding
  hyde_passage    a 1-2 sentence hypothetical answer           -> HyDE: embed this instead of the bare query

Only this LLM call is remote; retrieval stays local. Prints per-call + total token cost.
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import anthropic

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")

MODEL = "claude-haiku-4-5"
IN_PRICE, OUT_PRICE = 1.00 / 1e6, 5.00 / 1e6  # $/token

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["precise_lookup", "exploratory", "comparison"]},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "rewritten_query": {"type": "string"},
        "hyde_passage": {"type": "string"},
    },
    "required": ["intent", "keywords", "rewritten_query", "hyde_passage"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are the query-analysis stage of a retrieval pipeline over a historical archive: "
    "transcribed U.S. Civil War pension records (depositions, affidavits, widow's/minor's "
    "pension declarations, certificate and case numbers, names, places). For each user query:\n"
    "- intent: 'precise_lookup' if it targets a specific record/identifier/person; "
    "'exploratory' if it asks how/why or wants broad evidence; 'comparison' if it contrasts things.\n"
    "- keywords: the exact tokens a keyword index should match — surnames, given names, "
    "certificate/case numbers, place names. Omit stopwords.\n"
    "- rewritten_query: a cleaned, disambiguated restatement for semantic search.\n"
    "- hyde_passage: one or two sentences of a plausible archival answer, in period-appropriate "
    "language, to embed instead of the bare question (HyDE)."
)

QUERIES = [
    "pension certificate 366,181 minor children",
    "deposition of Cain Jenkins in the Adam Fields pension case",
    "how did a widow prove she was married to claim her husband's pension",
    "minor children pension of John and Molly Green",
]


def analyze(client, q):
    """Structured call with output_config.format; fall back to strict tool use on older SDKs."""
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=512, system=SYSTEM,
            messages=[{"role": "user", "content": q}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        data = json.loads(next(b.text for b in r.content if b.type == "text"))
    except TypeError:
        r = client.messages.create(
            model=MODEL, max_tokens=512, system=SYSTEM,
            messages=[{"role": "user", "content": q}],
            tools=[{"name": "plan", "description": "Return the retrieval plan.",
                    "strict": True, "input_schema": SCHEMA}],
            tool_choice={"type": "tool", "name": "plan"},
        )
        data = next(b.input for b in r.content if b.type == "tool_use")
    return data, r.usage


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    tot_in = tot_out = 0
    for q in QUERIES:
        plan, usage = analyze(client, q)
        tot_in += usage.input_tokens
        tot_out += usage.output_tokens
        cost = usage.input_tokens * IN_PRICE + usage.output_tokens * OUT_PRICE
        print("\n" + "=" * 92)
        print(f"QUERY: {q}")
        print(f"  intent         : {plan['intent']}")
        print(f"  keywords       : {plan['keywords']}")
        print(f"  rewritten_query: {plan['rewritten_query']}")
        print(f"  hyde_passage   : {plan['hyde_passage']}")
        print(f"  [tokens in/out: {usage.input_tokens}/{usage.output_tokens}  cost: ${cost:.5f}]")

    total = tot_in * IN_PRICE + tot_out * OUT_PRICE
    print("\n" + "#" * 92)
    print(f"TOTAL: {tot_in} in + {tot_out} out tokens  =  ${total:.5f} for {len(QUERIES)} queries")
    print(f"Per-query avg: ${total/len(QUERIES):.5f}  ->  ~${total/len(QUERIES)*1000:.2f} per 1,000 queries")
    print("#" * 92)


if __name__ == "__main__":
    main()
