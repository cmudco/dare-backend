"""Tool schema and executor for DARE PowerPoint artifacts."""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PptxToolArguments = Dict[str, object]
PptxToolResult = Dict[str, object]
PptxSlide = Dict[str, object]
PptxToolSchema = Dict[str, object]

MAX_PPTX_SLIDES = 20
MAX_PPTX_BULLETS_PER_SLIDE = 5
MAX_PPTX_BULLET_CHARS = 120
MAX_PPTX_TABLE_ROWS = 8


def execute_create_pptx(arguments: PptxToolArguments) -> PptxToolResult:
    """Validate and normalize the create_pptx tool arguments."""
    try:
        title = _clean_pptx_text(arguments.get("title"), 90)
        subtitle = _clean_pptx_text(arguments.get("subtitle"), 140)
        theme = arguments.get("theme") or {}
        slides = arguments.get("slides", [])

        if not title:
            return _error("Presentation title is required")

        if not isinstance(slides, list) or not slides:
            return _error("At least one presentation slide is required")

        if len(slides) > MAX_PPTX_SLIDES:
            return _error(f"Presentations can contain at most {MAX_PPTX_SLIDES} slides")

        if not isinstance(theme, dict):
            return _error("Theme must be an object")

        normalized_slides: List[PptxSlide] = []
        for index, slide in enumerate(slides):
            if not isinstance(slide, dict):
                return _error(f"Slide {index + 1} must be an object")

            result = _normalize_slide(index, slide)
            if not result.get("success"):
                return result

            normalized = result["slide"]
            if not isinstance(normalized, dict):
                return _error(f"Slide {index + 1} could not be normalized")

            slides_to_add = _expand_slide(normalized)
            if len(normalized_slides) + len(slides_to_add) > MAX_PPTX_SLIDES:
                return _error(
                    "Presentation content is too dense to fit within "
                    f"{MAX_PPTX_SLIDES} readable slides"
                )
            normalized_slides.extend(slides_to_add)

        return {
            "success": True,
            "ppt_config": {
                "title": title,
                "subtitle": subtitle,
                "theme": _normalize_theme(theme),
                "slides": normalized_slides,
            },
        }
    except Exception as e:
        logger.exception(f"Error executing create_pptx: {e}")
        return _error(str(e))


def get_create_pptx_tool_openai() -> PptxToolSchema:
    """Get create_pptx tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "create_pptx",
            "description": (
                "Create a NEW styled PowerPoint presentation artifact. "
                "Use this when the user asks for slides, a deck, a PowerPoint, "
                "or a presentation. Return a concise, presentation-ready deck "
                "with 5-10 slides unless the user asks for a different length. "
                "Each slide must use one of the supported layouts and include "
                "short slide text, not long paragraphs. Keep bullet slides to "
                "3-5 bullets, and keep each bullet under 120 characters. "
                "Use speakerNotes for citations, explanations, and details "
                "that should not crowd the slide. Do not answer with plain "
                "text when a PowerPoint is requested."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Presentation title.",
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Optional subtitle for the title slide.",
                    },
                    "theme": {
                        "type": "object",
                        "description": (
                            "Optional style hints. Use professional, "
                            "high-contrast colors. Do not overuse purple."
                        ),
                        "properties": {
                            "primaryColor": {
                                "type": "string",
                                "description": "Primary accent hex color, e.g. #2563EB.",
                            },
                            "accentColor": {
                                "type": "string",
                                "description": "Secondary accent hex color.",
                            },
                            "backgroundColor": {
                                "type": "string",
                                "description": "Slide background hex color.",
                            },
                            "textColor": {
                                "type": "string",
                                "description": "Primary text hex color.",
                            },
                            "mutedTextColor": {
                                "type": "string",
                                "description": "Secondary text hex color.",
                            },
                            "fontFamily": {
                                "type": "string",
                                "description": "PowerPoint font family, e.g. Aptos.",
                            },
                        },
                    },
                    "slides": _pptx_slides_schema(),
                },
                "required": ["title", "slides"],
            },
        },
    }


def get_create_pptx_tool_claude() -> PptxToolSchema:
    """Get create_pptx tool definition in Claude/Anthropic format."""
    openai_spec = get_create_pptx_tool_openai()
    func = openai_spec["function"]
    if not isinstance(func, dict):
        return {}
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"],
    }


def _normalize_slide(index: int, slide: PptxSlide) -> PptxToolResult:
    layout = str(slide.get("layout", "bullets"))
    if layout not in _allowed_layouts():
        return _error(f"Unsupported layout for slide {index + 1}: {layout}")

    slide_title = _clean_pptx_text(slide.get("title"), 90)
    if layout != "quote" and not slide_title:
        return _error(f"Slide {index + 1} requires a title")

    normalized: PptxSlide = {
        "layout": layout,
        "title": slide_title,
    }
    _copy_text_fields(slide, normalized)

    list_result = _copy_list_fields(index, slide, normalized)
    if not list_result.get("success"):
        return list_result

    table_result = _normalize_table_slide(index, slide, normalized, layout)
    if not table_result.get("success"):
        return table_result

    two_column_result = _normalize_two_column_slide(index, normalized, layout)
    if not two_column_result.get("success"):
        return two_column_result

    if layout in {"bullets", "summary"} and not normalized.get("bullets"):
        return _error(f"{layout} slide {index + 1} requires bullets")

    if layout == "quote" and not normalized.get("quote"):
        return _error(f"Quote slide {index + 1} requires quote")

    return {"success": True, "slide": normalized}


def _copy_text_fields(source: PptxSlide, target: PptxSlide) -> None:
    for optional_key in [
        "subtitle",
        "body",
        "quote",
        "attribution",
        "speakerNotes",
        "leftTitle",
        "rightTitle",
    ]:
        if source.get(optional_key):
            max_chars = 1200 if optional_key == "speakerNotes" else 220
            if optional_key in {"subtitle", "leftTitle", "rightTitle"}:
                max_chars = 90
            if optional_key in {"body", "quote"}:
                max_chars = 260
            target[optional_key] = _clean_pptx_text(source[optional_key], max_chars)


def _copy_list_fields(
    index: int,
    source: PptxSlide,
    target: PptxSlide,
) -> PptxToolResult:
    for list_key in ["bullets", "leftBullets", "rightBullets", "headers"]:
        values = source.get(list_key, [])
        if values:
            if not isinstance(values, list):
                return _error(f"{list_key} on slide {index + 1} must be a list")
            max_chars = 45 if list_key == "headers" else MAX_PPTX_BULLET_CHARS
            target[list_key] = _clean_pptx_list(values, max_chars)
    return {"success": True}


def _normalize_table_slide(
    index: int,
    source: PptxSlide,
    target: PptxSlide,
    layout: str,
) -> PptxToolResult:
    if layout != "table":
        return {"success": True}

    headers = target.get("headers", [])
    rows = source.get("rows", [])
    if not headers:
        return _error(f"Table slide {index + 1} requires headers")
    if not isinstance(headers, list):
        return _error(f"Headers on slide {index + 1} must be a list")
    if not isinstance(rows, list):
        return _error(f"Rows on slide {index + 1} must be a list")

    header_count = len(headers)
    normalized_rows: List[List[str]] = []
    for row_index, row in enumerate(rows[:MAX_PPTX_TABLE_ROWS]):
        if not isinstance(row, list) or len(row) != header_count:
            return _error(
                f"Table slide {index + 1}, row {row_index + 1} "
                f"must have {header_count} cells"
            )
        normalized_rows.append([_clean_pptx_text(cell, 80) for cell in row])

    if len(rows) > MAX_PPTX_TABLE_ROWS:
        _append_pptx_note(
            target,
            (
                f"{len(rows) - MAX_PPTX_TABLE_ROWS} table rows were "
                "omitted from the slide preview to preserve readability."
            ),
        )
    target["rows"] = normalized_rows
    return {"success": True}


def _normalize_two_column_slide(
    index: int,
    slide: PptxSlide,
    layout: str,
) -> PptxToolResult:
    if layout != "twoColumn":
        return {"success": True}

    if not slide.get("leftBullets") or not slide.get("rightBullets"):
        return _error(
            f"Two-column slide {index + 1} requires leftBullets and rightBullets"
        )

    for side_key, label in [
        ("leftBullets", "left column"),
        ("rightBullets", "right column"),
    ]:
        bullets = slide.get(side_key, [])
        if isinstance(bullets, list) and len(bullets) > MAX_PPTX_BULLETS_PER_SLIDE:
            slide[side_key] = bullets[:MAX_PPTX_BULLETS_PER_SLIDE]
            extra = "; ".join(
                str(item) for item in bullets[MAX_PPTX_BULLETS_PER_SLIDE:]
            )
            _append_pptx_note(slide, f"Additional {label} points: {extra}")

    return {"success": True}


def _expand_slide(slide: PptxSlide) -> List[PptxSlide]:
    if slide.get("layout") in {"bullets", "summary"}:
        return _split_pptx_bullet_slide(slide)
    return [slide]


def _normalize_theme(theme: Dict[object, object]) -> Dict[str, str]:
    allowed_keys = {
        "primaryColor",
        "accentColor",
        "backgroundColor",
        "textColor",
        "mutedTextColor",
        "fontFamily",
    }
    return {
        str(key): str(value).strip()
        for key, value in theme.items()
        if key in allowed_keys and str(value).strip()
    }


def _clean_pptx_text(value: object, max_chars: Optional[int] = None) -> str:
    text = str(value or "").strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def _clean_pptx_list(values: List[object], max_chars: int) -> List[str]:
    cleaned = []
    for value in values:
        text = _clean_pptx_text(value, max_chars)
        if text:
            cleaned.append(text)
    return cleaned


def _append_pptx_note(slide: PptxSlide, note: str) -> None:
    existing = slide.get("speakerNotes")
    slide["speakerNotes"] = f"{existing}\n\n{note}" if existing else note


def _split_pptx_bullet_slide(slide: PptxSlide) -> List[PptxSlide]:
    bullets = slide.get("bullets") or []
    if not isinstance(bullets, list):
        return [slide]

    if len(bullets) <= MAX_PPTX_BULLETS_PER_SLIDE:
        return [slide]

    split_slides = []
    for chunk_index, start in enumerate(
        range(0, len(bullets), MAX_PPTX_BULLETS_PER_SLIDE)
    ):
        chunk = bullets[start : start + MAX_PPTX_BULLETS_PER_SLIDE]
        next_slide = dict(slide)
        next_slide["bullets"] = chunk
        if chunk_index:
            next_slide["title"] = f"{slide['title']} (continued)"
            next_slide.pop("body", None)
        split_slides.append(next_slide)
    return split_slides


def _allowed_layouts() -> set[str]:
    return {
        "title",
        "section",
        "bullets",
        "twoColumn",
        "table",
        "quote",
        "summary",
    }


def _error(message: str) -> PptxToolResult:
    return {"success": False, "error": message}


def _pptx_slides_schema() -> PptxToolSchema:
    return {
        "type": "array",
        "description": (
            "Ordered slides. Start with a title slide. Keep each slide "
            "readable: 3-5 bullets, short phrases, and no paragraph-length "
            "bullet text."
        ),
        "minItems": 1,
        "maxItems": 20,
        "items": {
            "type": "object",
            "properties": {
                "layout": {
                    "type": "string",
                    "enum": [
                        "title",
                        "section",
                        "bullets",
                        "twoColumn",
                        "table",
                        "quote",
                        "summary",
                    ],
                },
                "title": {
                    "type": "string",
                    "description": "Slide title. Required except quote slides.",
                },
                "subtitle": {
                    "type": "string",
                    "description": "Optional subtitle, mainly for title slides.",
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Brief supporting text for section or bullet slides. "
                        "Keep under 2 short sentences."
                    ),
                },
                "bullets": _bullet_schema(
                    "Bullet points for bullets or summary slides. Use 3-5 "
                    "concise bullets, each under 120 characters."
                ),
                "leftTitle": {
                    "type": "string",
                    "description": "Left column heading for twoColumn slides.",
                },
                "leftBullets": _bullet_schema(
                    "Left column bullets for twoColumn slides. Keep each bullet short."
                ),
                "rightTitle": {
                    "type": "string",
                    "description": "Right column heading for twoColumn slides.",
                },
                "rightBullets": _bullet_schema(
                    "Right column bullets for twoColumn slides. Keep each bullet short."
                ),
                "headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Table column headers for table slides.",
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "maxItems": 8,
                    "description": (
                        "Table rows. Each row must match headers length. "
                        "Use at most 8 rows."
                    ),
                },
                "quote": {
                    "type": "string",
                    "description": "Quote text for quote slides.",
                },
                "attribution": {
                    "type": "string",
                    "description": "Quote attribution.",
                },
                "speakerNotes": {
                    "type": "string",
                    "description": (
                        "Optional presenter notes with richer detail, citations, "
                        "or overflow content that should not appear on the slide."
                    ),
                },
            },
            "required": ["layout"],
        },
    }


def _bullet_schema(description: str) -> PptxToolSchema:
    return {
        "type": "array",
        "items": {"type": "string", "maxLength": 120},
        "minItems": 1,
        "maxItems": 5,
        "description": description,
    }
