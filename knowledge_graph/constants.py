from django.db import models

APP_NAME = "knowledge_graph"

MAX_RELATION_LABELS = 12
MAX_PAIRS_PER_CHUNK = 15
MAX_IMPLIED_PAIRS_PER_CHUNK = 6
MAX_REACH_PAIRS_PER_CHUNK = 6
STRONG_PAIR_CHUNKS = 3
DISCOVERY_SAMPLE_CHUNKS = 200
DISCOVERY_BATCH_CHUNKS = 10
ALIAS_MERGE_THRESHOLD = 0.8
RELATION_VERIFY_THRESHOLD = 0.7
ALIAS_QUESTIONS_PER_REQUEST = 100
MAX_ALIAS_CANDIDATES = 1000
MAX_FAILED_CHUNK_SHARE = 0.1
MAX_PROPOSAL_SENTENCES = 150
PROPOSAL_BATCH_SENTENCES = 50
MAX_PROPOSED_TYPES = 8
MAX_QUOTE_CHARS = 280
QUOTE_CONTEXT_CHARS = 90
LABEL_EXAMPLES = 3
MIN_FAILED_CHUNKS_TO_STOP = 3
LINKING_EXCLUDED_KINDS = ("date", "url", "doi")
# Measured on the Watergate and anime builds: a pair question plus its check.
ESTIMATED_TOKENS_PER_QUESTION = 100
ESTIMATED_TOKENS_PER_LABEL = 60
# Measured on the midterms build: 400 alias questions used 97,624 input tokens.
ESTIMATED_TOKENS_PER_ALIAS_QUESTION = 250
# Reasoning included; measured across the background models.
ESTIMATED_DISCOVERY_OUTPUT_TOKENS = 1500
ESTIMATED_DISCOVERY_PROMPT_TOKENS = 500
CHARS_PER_TOKEN = 4
# Types no single sentence can prove; the model keeps proposing them if allowed.
VAGUE_RELATION_TYPES = frozenset(
    {
        "affected",
        "associated_with",
        "connected_to",
        "engaged_with",
        "impacted",
        "influenced",
        "interacted_with",
        "involved_in",
        "linked_to",
        "related_to",
        "relates_to",
    }
)


class GraphStatus(models.TextChoices):
    PREPARING = "preparing", "Getting entities ready"
    READY_FREE = "free", "Entities and aliases ready"
    DISCOVERING = "discovering", "Finding relationship types"
    AWAITING_APPROVAL = "awaiting_approval", "Waiting for cost approval"
    BUILDING = "building", "Reading relationships"
    READY = "ready", "Relationships ready"
    FAILED = "failed", "Failed"


class LabelSource(models.TextChoices):
    DISCOVERED = "discovered", "Suggested by the model"
    USER = "user", "Added by you"


class AliasSource(models.TextChoices):
    SELF = "self", "Own name"
    RULE = "rule", "Name rule"
    DECISIONS = "decisions", "Model check"
    MANUAL = "manual", "Changed by you"
