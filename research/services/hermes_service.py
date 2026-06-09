"""
Thin client for the Hermes agent gateway — the DARE-owned adapter boundary.

Hermes is driven over REST: `POST /v1/runs` starts an async run (the soul rides
in `instructions`, continuity via `session_id`), and `GET /v1/runs/{id}/events`
streams the reply as SSE (`message.delta`) plus tool-call provenance. DARE never
gives Hermes DB access; this is the only place DARE talks to Hermes.
"""

import json
import logging
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class HermesService:
    """REST client for the Hermes gateway (drive + SSE stream)."""

    def __init__(self):
        self.base_url = settings.HERMES_GATEWAY_URL.rstrip("/")
        self.api_key = settings.HERMES_API_KEY

    def _headers(self, *, json_body=False):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def start_run(self, *, input_text, instructions, session_id, timeout=30):
        """
        Start an async run. The soul-file content rides in `instructions`;
        `session_id` gives persistent cross-run memory. Returns the gateway JSON
        (``{"run_id": ..., "status": "started"}``).
        """
        resp = requests.post(
            f"{self.base_url}/v1/runs",
            headers=self._headers(json_body=True),
            json={
                "input": input_text,
                "instructions": instructions,
                "session_id": session_id,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def stream_events(self, hermes_run_id, timeout=300):
        """
        Stream a run's SSE events as parsed dicts. Each event carries an
        ``event`` key: ``message.delta`` (``delta`` token), ``tool.started`` /
        ``tool.completed``, ``run.completed``, etc.
        """
        with requests.get(
            f"{self.base_url}/v1/runs/{hermes_run_id}/events",
            headers=self._headers(),
            stream=True,
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload:
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(
                        "Hermes SSE: could not parse event line: %s", payload[:200]
                    )

    def provision_soul(self, content):
        """
        Write DARE's canonical soul into the gateway profile's SOUL.md — the
        anchor (slot #1 of the system prompt) that Hermes reads fresh each run.
        This is how DARE's soul actually governs, kept in sync on every edit/run.

        No-op (returns False) if syncing is disabled or the path isn't writable;
        the per-run ``instructions`` overlay then remains the fallback.
        """
        if not settings.HERMES_SYNC_SOUL:
            return False
        try:
            Path(settings.HERMES_SOUL_PATH).write_text(content or "", encoding="utf-8")
            return True
        except OSError as exc:
            logger.warning(
                "Could not provision Hermes SOUL.md at %s: %s",
                settings.HERMES_SOUL_PATH,
                exc,
            )
            return False

    def read_agent_memory(self):
        """
        Read the Hermes profile's operational memory files (read-only), so DARE
        can show what the agent holds: the on-disk SOUL.md (which DARE provisions
        — so it mirrors the project's soul), plus MEMORY.md / USER.md that Hermes
        auto-writes as it learns. Returns {} values for files that don't exist yet.
        """
        home = Path(settings.HERMES_SOUL_PATH).parent

        def _read(path):
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""

        return {
            "soul": _read(home / "SOUL.md"),
            "memory": _read(home / "memories" / "MEMORY.md"),
            "user": _read(home / "memories" / "USER.md"),
        }

    def get_run(self, hermes_run_id, timeout=30):
        """Poll a run's status/result (``{status, output, usage, model, ...}``)."""
        resp = requests.get(
            f"{self.base_url}/v1/runs/{hermes_run_id}",
            headers=self._headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_usage(self, hermes_run_id, timeout=30):
        """
        Fetch a finished run's token usage from the run summary. Best-effort
        audit data — returns {} rather than raising, so a usage hiccup never
        fails a run that otherwise completed.
        """
        try:
            usage = self.get_run(hermes_run_id, timeout=timeout).get("usage")
        except (requests.RequestException, ValueError) as exc:
            logger.warning(
                "Could not fetch Hermes usage for run %s: %s", hermes_run_id, exc
            )
            return {}
        return usage if isinstance(usage, dict) else {}


_hermes_service = None


def get_hermes_service():
    """Return the shared HermesService instance."""
    global _hermes_service
    if _hermes_service is None:
        _hermes_service = HermesService()
    return _hermes_service
