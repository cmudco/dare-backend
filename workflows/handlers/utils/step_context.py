"""
Structured context passing for step nodes.

Owns the typed representation of the upstream outputs a step receives and the
XML renderer that turns them into a provider-agnostic context block for the
LLM prompt. Consumed by ``StepMessagePreparer`` in ``message_preparers.py``.

The rendered block looks like::

    <workflow_context>
      <upstream_node id="<source_id>" label="<label>" type="<source_type>">
        <output><![CDATA[<output text>]]></output>
      </upstream_node>
      ...
    </workflow_context>
"""
from dataclasses import dataclass
from html import escape as html_escape
from typing import Any, Dict, List

from .constants import WorkflowContextTag
from .validation_helpers import MetadataValidator


@dataclass(frozen=True)
class StepContextEntry:
    """One upstream producer's contribution to a downstream step's context."""
    source_id: str
    source_type: str
    output: str
    label: str = ""


class StepContextBuilder:
    """
    Convert the raw ``previous_results`` payload produced by
    ``execution_routing.get_dep_results`` into typed ``StepContextEntry`` items.
    Skipped entries (per ``MetadataValidator.is_skipped``) and entries with
    empty output are omitted.
    """

    @staticmethod
    def build(
        previous_results: Dict[str, Dict[str, Any]]
    ) -> List[StepContextEntry]:
        entries: List[StepContextEntry] = []
        for source_id, payload in (previous_results or {}).items():
            if not isinstance(payload, dict):
                continue
            output = payload.get("output")
            if not output:
                continue
            if MetadataValidator.is_skipped(payload.get("metadata") or {}):
                continue
            entries.append(
                StepContextEntry(
                    source_id=source_id,
                    source_type=payload.get("node_type") or "",
                    output=output,
                    label=payload.get("label") or "",
                )
            )
        return entries


class ContextRenderer:
    """
    Render step-context entries as an XML block for inclusion in an LLM prompt.
    Output text is wrapped in ``CDATA`` so arbitrary upstream content cannot
    collide with the surrounding prompt.
    """

    _CDATA_END = "]]>"
    _CDATA_ESCAPE = "]]]]><![CDATA[>"

    @staticmethod
    def _cdata_safe(text: str) -> str:
        # ']]>' is the only sequence forbidden inside CDATA; split-and-rejoin
        # is the XML-standard escape.
        return text.replace(
            ContextRenderer._CDATA_END, ContextRenderer._CDATA_ESCAPE
        )

    @staticmethod
    def render_xml(entries: List[StepContextEntry]) -> str:
        """
        Produce the ``<workflow_context>`` block. Returns an empty string when
        there are no usable entries so the caller can omit the block entirely.
        """
        if not entries:
            return ""

        lines: List[str] = [f"<{WorkflowContextTag.BLOCK}>"]
        for entry in entries:
            if not entry.output:
                continue
            attrs = (
                f'id="{html_escape(entry.source_id, quote=True)}" '
                f'label="{html_escape(entry.label, quote=True)}" '
                f'type="{html_escape(entry.source_type, quote=True)}"'
            )
            lines.append(f"  <{WorkflowContextTag.UPSTREAM} {attrs}>")
            lines.append(
                f"    <{WorkflowContextTag.OUTPUT}>"
                f"<![CDATA[{ContextRenderer._cdata_safe(entry.output)}]]>"
                f"</{WorkflowContextTag.OUTPUT}>"
            )
            lines.append(f"  </{WorkflowContextTag.UPSTREAM}>")
        lines.append(f"</{WorkflowContextTag.BLOCK}>")

        return "\n".join(lines)


__all__ = [
    "StepContextEntry",
    "StepContextBuilder",
    "ContextRenderer",
]
