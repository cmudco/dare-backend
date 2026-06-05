"""
Initial set of feature flag keys seeded into the database. Keys are stored in
snake_case; the DRF camelCase renderer converts them to camelCase on the wire,
e.g. ``enable_byok`` -> ``enableByok``.

``DEFAULT_FLAG_DEFINITIONS`` is consumed by the data migration that seeds the
``FeatureFlag`` table on first deploy. Defaults match the most conservative
production tier (DARE production) so behavior is preserved on rollout.
"""

DEFAULT_FLAG_DEFINITIONS = [
    {
        "key": "enable_byok",
        "description": "Bring Your Own Key — show the API Keys section in Settings.",
        "default_enabled": False,
    },
    {
        "key": "enable_litellm_wallet",
        "description": (
            "Allow users to add and select LiteLLM proxy keys as the active "
            "wallet. When disabled, LiteLLM wallets are hidden in the UI and "
            "the wallet router falls back to DARE."
        ),
        "default_enabled": True,
    },
    {
        "key": "enable_image_generation",
        "description": "Show image generation controls in the chat configuration panel.",
        "default_enabled": True,
    },
    {
        "key": "enable_artifacts",
        "description": "Long-form document/artifacts generation with sidecar UI.",
        "default_enabled": False,
    },
    {
        "key": "enable_audio_transcription",
        "description": "Whisper/Gemini audio-to-text transcription support.",
        "default_enabled": False,
    },
    {
        "key": "enable_voice_input",
        "description": "Push-to-talk voice input (V1).",
        "default_enabled": False,
    },
    {
        "key": "enable_debug_logs",
        "description": "Verbose console debug logging on the frontend.",
        "default_enabled": False,
    },
    {
        "key": "enable_mcp",
        "description": "Model Context Protocol server integrations.",
        "default_enabled": True,
    },
    {
        "key": "enable_memory",
        "description": "Conversation memory feature (seed/recall).",
        "default_enabled": False,
    },
    {
        "key": "enable_research",
        "description": "Show and allow access to the Research workspace.",
        "default_enabled": False,
    },
    {
        "key": "enable_sharing",
        "description": "Publish/share prompts, conversations, and workflows.",
        "default_enabled": False,
    },
]
