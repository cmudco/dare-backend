APP_NAME = "research"

ENABLE_RESEARCH_FLAG = "enable_research"

RESEARCH_ROLE_POLICIES = {
    "main_assistant": {
        "allowed_tools": [],
        "allowed_output_destinations": ["run_log"],
        "default_output_destination": "run_log",
        "capability_policy": {
            "can_create_staging": False,
            "can_update_review_metadata": False,
            "can_propose_memory": False,
            "can_propose_artifacts": False,
            "can_approve_knowledge": False,
        },
    },
    "scout": {
        "allowed_tools": ["pubmed", "web", "consensus"],
        "allowed_output_destinations": ["staging"],
        "default_output_destination": "staging",
        "capability_policy": {
            "can_create_staging": True,
            "can_update_review_metadata": False,
            "can_propose_memory": False,
            "can_propose_artifacts": False,
            "can_approve_knowledge": False,
        },
    },
    "librarian": {
        "allowed_tools": ["pubmed", "web"],
        "allowed_output_destinations": ["run_log"],
        "default_output_destination": "run_log",
        "capability_policy": {
            "can_create_staging": False,
            "can_update_review_metadata": False,
            "can_propose_memory": False,
            "can_propose_artifacts": False,
            "can_approve_knowledge": False,
        },
    },
    "paper_assistant": {
        "allowed_tools": ["pubmed", "web"],
        "allowed_output_destinations": ["memory_proposals", "staging"],
        "default_output_destination": "memory_proposals",
        "capability_policy": {
            "can_create_staging": True,
            "can_update_review_metadata": False,
            "can_propose_memory": True,
            "can_propose_artifacts": False,
            "can_approve_knowledge": False,
        },
    },
    "critic": {
        "allowed_tools": ["scite", "pubmed", "web"],
        "allowed_output_destinations": ["review_metadata"],
        "default_output_destination": "review_metadata",
        "capability_policy": {
            "can_create_staging": False,
            "can_update_review_metadata": True,
            "can_propose_memory": False,
            "can_propose_artifacts": False,
            "can_approve_knowledge": False,
        },
    },
    "presentation_assistant": {
        "allowed_tools": [],
        "allowed_output_destinations": ["artifact_proposals"],
        "default_output_destination": "artifact_proposals",
        "capability_policy": {
            "can_create_staging": False,
            "can_update_review_metadata": False,
            "can_propose_memory": False,
            "can_propose_artifacts": True,
            "can_approve_knowledge": False,
        },
    },
}

RESEARCH_ETHICS_TEMPLATE_BODY = """Purpose:
- Preserve careful, non-fabricating scholarship.
- Keep uncertainty visible.
- Never overstate what a source supports.

Standards:
- Every citation must be real and verifiable.
- Separate evidence, interpretation, and speculation.
- Flag ethical nuance instead of flattening it.
- Prefer primary sources when possible.
"""

EMPIRICAL_RIGOR_TEMPLATE_BODY = """Purpose:
- Keep empirical claims grounded in method quality and reproducibility.
- Prefer direct evidence over plausible narrative.

Standards:
- Surface sample size, method, population, and effect size when available.
- Distinguish correlation from causation.
- Flag replication status and uncertainty.
- Prefer pre-registered and primary studies where possible.
"""

SOUL_FILE_TEMPLATES = [
    {
        "key": "research-ethics",
        "name": "Research Ethics",
        "description": "Careful, non-fabricating scholarship for ethics, policy, and governance work.",
        "body": RESEARCH_ETHICS_TEMPLATE_BODY,
    },
    {
        "key": "empirical-rigor",
        "name": "Empirical Rigor",
        "description": "Methods-first standards for empirical and data-heavy research.",
        "body": EMPIRICAL_RIGOR_TEMPLATE_BODY,
    },
    {
        "key": "custom",
        "name": "Start Blank",
        "description": "A blank soul file for custom project standards.",
        "body": "",
    },
]

RESEARCH_METADATA = {
    "roles": [
        {
            "key": "main_assistant",
            "name": "Main Assistant",
            "status": "planned",
            "description": "Routes delegated research work to specialist roles.",
        },
        {
            "key": "scout",
            "name": "Scout",
            "status": "planned",
            "description": "Finds candidate sources and returns them to staging.",
        },
        {
            "key": "librarian",
            "name": "Librarian",
            "status": "planned",
            "description": "Normalizes source metadata and preserves rationale.",
        },
        {
            "key": "paper_assistant",
            "name": "Paper-Specific Assistant",
            "status": "planned",
            "description": "Maintains paper-scoped claims, notes, and memory proposals.",
        },
        {
            "key": "critic",
            "name": "Critic",
            "status": "planned",
            "description": "Pressure-tests source support and citation context.",
        },
        {
            "key": "presentation_assistant",
            "name": "Presentation Assistant",
            "status": "planned",
            "description": "Drafts artifact plans from approved or selected material.",
        },
    ],
    "runtime": {
        "key": "hermes",
        "status": "adapter_ready",
        "message": "Hermes run auditing is available; live role execution is not connected yet.",
    },
    "soul_file_templates": SOUL_FILE_TEMPLATES,
}

__all__ = [
    "APP_NAME",
    "EMPIRICAL_RIGOR_TEMPLATE_BODY",
    "ENABLE_RESEARCH_FLAG",
    "RESEARCH_METADATA",
    "RESEARCH_ROLE_POLICIES",
    "RESEARCH_ETHICS_TEMPLATE_BODY",
    "SOUL_FILE_TEMPLATES",
]
