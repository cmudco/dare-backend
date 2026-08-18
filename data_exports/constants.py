"""Bundle schema and the contract between export and restore."""

from django.db import models

SCHEMA = "dare-export-v2"

MANIFEST_PATH = "manifest.json"
PROFILE_PATH = "profile.json"
MEMORIES_PATH = "memories.json"
PROMPTS_PATH = "prompts.json"
CONVERSATIONS_PATH = "conversations.json"
WORKFLOWS_PATH = "workflows.json"

# A restore rebuilds every row, so an archive is bounded by what one request
# can hold in memory rather than by what the database could return.
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_CONVERSATIONS = 5000
MAX_MESSAGES = 100_000
MAX_PROMPTS = 2000
MAX_WORKFLOWS = 500


class ExportScope(models.TextChoices):
    FULL = "full", "Everything"
    MEMORIES = "memories", "Memories only"


# Uploaded file contents are deliberately absent: blobs dominate archive size
# and a restore cannot re-point them at a new account's storage. Conversations
# keep their transcript; file selections reset.
EXCLUSIONS = (
    "Uploaded file contents and the file library",
    "Generated artifacts, tool-call records and retrieval traces",
    "Workflow run history",
    "Billing history, wallet balances and API keys",
)
