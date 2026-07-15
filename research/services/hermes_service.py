"""
Thin client for the Hermes agent gateway — the DARE-owned adapter boundary.

Hermes is driven over REST: `POST /v1/runs` starts an async run (the soul rides
in `instructions`, continuity via `session_id`), and `GET /v1/runs/{id}/events`
streams the reply as SSE (`message.delta`) plus tool-call provenance. DARE never
gives Hermes DB access; this is the only place DARE talks to Hermes.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
_SAFE_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


def safe_hermes_usage(value):
    """Keep only numeric token counters from an upstream usage object."""
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in _SAFE_USAGE_FIELDS
        if isinstance(value.get(key), (int, float))
        and not isinstance(value.get(key), bool)
    }


@dataclass(frozen=True)
class HermesStopResult:
    """Safe, structured result of one Hermes stop request."""

    code: str
    acknowledged: bool = False
    http_status: int | None = None
    upstream_status: str = ""
    detail: str = ""


class HermesService:
    """REST client for the Hermes gateway (drive + SSE stream)."""

    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or settings.HERMES_GATEWAY_URL).rstrip("/")
        self.api_key = api_key or settings.HERMES_API_KEY

    def _headers(self, *, json_body=False):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def start_run(
        self, *, input_text, instructions, session_id, session_key=None, timeout=30
    ):
        """
        Start an async run. The soul-file content rides in `instructions`;
        `session_id` gives persistent cross-run memory within one mode's thread.
        `session_key` (Hermes's official X-Hermes-Session-Key) is the stable
        long-term memory scope — one per research workspace, shared by all of
        its modes. Returns the gateway JSON (``{"run_id": ..., "status": ...}``).
        """
        headers = self._headers(json_body=True)
        if session_key:
            headers["X-Hermes-Session-Key"] = session_key
        resp = requests.post(
            f"{self.base_url}/v1/runs",
            headers=headers,
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

    def stop_run(self, hermes_run_id, timeout=15):
        """Request cancellation and return a safe, structured transport result."""
        try:
            resp = requests.post(
                f"{self.base_url}/v1/runs/{hermes_run_id}/stop",
                headers=self._headers(),
                timeout=timeout,
            )
        except requests.Timeout:
            return HermesStopResult(
                code="stop_timeout",
                detail="The Hermes stop request timed out.",
            )
        except requests.ConnectionError:
            return HermesStopResult(
                code="stop_connection_failure",
                detail="Could not connect to Hermes for the stop request.",
            )
        except requests.RequestException as exc:
            logger.warning("Could not stop Hermes run %s: %s", hermes_run_id, exc)
            return HermesStopResult(
                code="stop_transport_error",
                detail="The Hermes stop request failed before a response was received.",
            )

        http_status = resp.status_code
        if http_status == 404:
            return HermesStopResult(
                code="hermes_run_not_found",
                http_status=http_status,
                detail="Hermes did not have an active stop target for this run.",
            )
        if http_status >= 500:
            return HermesStopResult(
                code="stop_upstream_error",
                http_status=http_status,
                detail="Hermes returned a server error for the stop request.",
            )
        if not 200 <= http_status < 300:
            return HermesStopResult(
                code="stop_http_error",
                http_status=http_status,
                detail="Hermes rejected the stop request.",
            )

        try:
            data = resp.json()
        except ValueError:
            return HermesStopResult(
                code="stop_invalid_json",
                http_status=http_status,
                detail="Hermes returned invalid JSON for the stop request.",
            )
        if not isinstance(data, dict):
            return HermesStopResult(
                code="stop_invalid_json",
                http_status=http_status,
                detail="Hermes returned an invalid stop response.",
            )

        upstream_status = str(data.get("status") or "").lower()
        if upstream_status == "stopping":
            return HermesStopResult(
                code="stop_acknowledged",
                acknowledged=True,
                http_status=http_status,
                upstream_status=upstream_status,
            )
        return HermesStopResult(
            code="stop_unexpected_response",
            http_status=http_status,
            upstream_status=upstream_status,
            detail="Hermes returned an unexpected stop status.",
        )

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
        return safe_hermes_usage(usage)


_hermes_service = None


def get_hermes_service(project=None):
    """Return a HermesService for ``project`` — its own per-project Hermes endpoint
    when one is provisioned, else the shared default instance. A per-project
    endpoint (one gateway per project) is how DARE keeps each project's agent
    memory and credentials separate; with none set this is behaviour-neutral and
    returns the shared default."""
    base = getattr(project, "hermes_base_url", "") if project else ""
    if base:
        key = getattr(project, "hermes_api_key", "") or settings.HERMES_API_KEY
        return HermesService(base_url=base, api_key=key)
    global _hermes_service
    if _hermes_service is None:
        _hermes_service = HermesService()
    return _hermes_service
