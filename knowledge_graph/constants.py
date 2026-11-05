from django.db import models

APP_NAME = "knowledge_graph"

MAX_RELATION_LABELS = 12
MAX_PAIRS_PER_CHUNK = 15
DISCOVERY_SAMPLE_CHUNKS = 12
DISCOVERY_BATCH_CHUNKS = 4
ALIAS_MERGE_THRESHOLD = 0.8
RELATION_CHOICE_THRESHOLD = 0.6
RELATION_VERIFY_THRESHOLD = 0.7
ALIAS_QUESTIONS_PER_REQUEST = 100
MAX_ALIAS_CANDIDATES = 400
LINKING_EXCLUDED_KINDS = ("date", "url", "doi")
# Measured on the Watergate and anime builds: a pair question plus its check.
ESTIMATED_TOKENS_PER_QUESTION = 100
ESTIMATED_TOKENS_PER_LABEL = 60
ESTIMATED_DISCOVERY_OUTPUT_TOKENS = 400
ESTIMATED_DISCOVERY_PROMPT_TOKENS = 400
CHARS_PER_TOKEN = 4


class GraphStatus(models.TextChoices):
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
