"""Generate PDF files from DARE artifact JSON specs."""

from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any, Dict, Iterable, List

import weasyprint

from dare_tools.services.pptx_generator import DEFAULT_THEME


def generate_docx_pdf_bytes(config: Dict[str, Any]) -> bytes:
    """Build a readable PDF from a validated DARE document config."""
    title = escape(str(config.get("title") or "Document"))
    blocks = config.get("blocks") or []
    body = "\n".join(_docx_block_to_html(block) for block in blocks)
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          @page {{ size: Letter; margin: 0.65in; }}
          body {{
            color: #0f172a;
            font-family: Arial, sans-serif;
            font-size: 11pt;
            line-height: 1.45;
          }}
          h1 {{ border-bottom: 1px solid #cbd5e1; font-size: 26pt; padding-bottom: 12px; }}
          h2 {{ font-size: 19pt; margin-top: 24px; }}
          h3 {{ font-size: 15pt; margin-top: 20px; }}
          h4, h5 {{ font-size: 12pt; margin-top: 16px; }}
          blockquote {{
            border-left: 4px solid #94a3b8;
            color: #475569;
            margin-left: 0;
            padding-left: 14px;
          }}
          table {{ border-collapse: collapse; margin: 16px 0; width: 100%; }}
          th {{ background: #2563eb; color: #fff; }}
          th, td {{ border: 1px solid #cbd5e1; padding: 7px; text-align: left; }}
        </style>
      </head>
      <body>
        <h1>{title}</h1>
        {body}
      </body>
    </html>
    """
    return _html_to_pdf(html)


def generate_pptx_pdf_bytes(config: Dict[str, Any]) -> bytes:
    """Build a preview-matched PDF from a validated DARE presentation config."""
    theme = {**DEFAULT_THEME, **(config.get("theme") or {})}
    slides = config.get("slides") or []
    slide_html = "\n".join(
        _pptx_slide_to_html(slide, index + 1, len(slides), theme)
        for index, slide in enumerate(slides)
    )
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          @page {{ size: 13.333in 7.5in; margin: 0; }}
          * {{ box-sizing: border-box; }}
          body {{ margin: 0; }}
          .slide {{
            background: {theme["backgroundColor"]};
            color: {theme["textColor"]};
            font-family: {escape(str(theme["fontFamily"]))}, Arial, sans-serif;
            height: 7.5in;
            overflow: hidden;
            padding: 0.65in;
            page-break-after: always;
            position: relative;
            width: 13.333in;
          }}
          .accent {{ background: {theme["primaryColor"]}; height: 0.08in; left: 0; position: absolute; top: 0; width: 100%; }}
          .muted {{ color: {theme["mutedTextColor"]}; }}
          h1 {{ font-size: 42pt; line-height: 1.05; margin: 0; }}
          h2 {{ font-size: 31pt; line-height: 1.08; margin: 0 0 0.38in; }}
          p {{ margin: 0; }}
          ul {{ font-size: 22pt; line-height: 1.25; margin: 0; padding-left: 0.34in; }}
          li {{ margin-bottom: 0.18in; }}
          .center {{ display: flex; flex-direction: column; height: 100%; justify-content: center; max-width: 72%; }}
          .body {{ font-size: 17pt; line-height: 1.35; margin-bottom: 0.28in; }}
          .columns {{ display: grid; gap: 0.38in; grid-template-columns: 1fr 1fr; }}
          .card {{ background: #fff; border: 1px solid #dde3ea; border-radius: 0.08in; padding: 0.28in; }}
          .card h3 {{ font-size: 19pt; margin: 0 0 0.24in; }}
          .card ul {{ font-size: 16pt; }}
          table {{ border-collapse: collapse; font-size: 13pt; width: 100%; }}
          th {{ background: {theme["primaryColor"]}; color: #fff; }}
          th, td {{ border: 1px solid #cbd5e1; padding: 0.1in; text-align: left; }}
          blockquote {{ font-size: 32pt; font-weight: 700; line-height: 1.15; margin: 0; max-width: 9.2in; }}
          .summary {{ display: grid; gap: 0.28in; grid-template-columns: repeat(3, 1fr); }}
          .summary p {{ font-size: 19pt; font-weight: 700; line-height: 1.18; }}
          .number {{ color: {theme["primaryColor"]}; font-size: 14pt; font-weight: 700; margin-bottom: 0.2in; }}
          .footer {{ bottom: 0.22in; color: {theme["mutedTextColor"]}; font-size: 9pt; position: absolute; right: 0.72in; }}
        </style>
      </head>
      <body>{slide_html}</body>
    </html>
    """
    return _html_to_pdf(html)


def _html_to_pdf(html: str) -> bytes:
    stream = BytesIO()
    weasyprint.HTML(string=html).write_pdf(stream)
    return stream.getvalue()


def _docx_block_to_html(block: Dict[str, Any]) -> str:
    block_type = block.get("type")
    if block_type == "heading":
        level = min(max(int(block.get("level") or 2), 1), 4) + 1
        return f"<h{level}>{escape(str(block.get('text') or ''))}</h{level}>"
    if block_type == "paragraph":
        alignment = (
            block.get("alignment")
            if block.get("alignment") in {"left", "center", "right"}
            else "left"
        )
        return f'<p style="text-align:{alignment};">{escape(str(block.get("text") or ""))}</p>'
    if block_type == "list":
        tag = "ol" if block.get("ordered") else "ul"
        items = "".join(
            f"<li>{escape(str(item))}</li>" for item in block.get("items") or []
        )
        return f"<{tag}>{items}</{tag}>"
    if block_type == "blockquote":
        return f"<blockquote>{escape(str(block.get('text') or ''))}</blockquote>"
    if block_type == "table":
        headers = "".join(
            f"<th>{escape(str(header))}</th>" for header in block.get("headers") or []
        )
        rows = "".join(_table_row_to_html(row) for row in block.get("rows") or [])
        return f"<table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"
    return ""


def _table_row_to_html(row: Iterable[Any]) -> str:
    return "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"


def _pptx_slide_to_html(
    slide: Dict[str, Any], current: int, total: int, theme: Dict[str, str]
) -> str:
    layout = slide.get("layout") or "bullets"
    if layout == "title":
        content = _pptx_title_slide(slide)
    elif layout == "section":
        content = _pptx_section_slide(slide)
    elif layout == "twoColumn":
        content = _pptx_two_column_slide(slide)
    elif layout == "table":
        content = _pptx_table_slide(slide)
    elif layout == "quote":
        content = _pptx_quote_slide(slide)
    elif layout == "summary":
        content = _pptx_summary_slide(slide)
    else:
        content = _pptx_bullets_slide(slide)

    return (
        '<section class="slide">'
        '<div class="accent"></div>'
        f"{content}"
        f'<div class="footer">{current} / {total}</div>'
        "</section>"
    )


def _pptx_title_slide(slide: Dict[str, Any]) -> str:
    subtitle = slide.get("subtitle")
    subtitle_html = (
        f'<p class="muted" style="font-size:21pt;margin-top:0.34in;">{escape(str(subtitle))}</p>'
        if subtitle
        else ""
    )
    return f'<div class="center"><h1>{escape(str(slide.get("title") or "Presentation"))}</h1>{subtitle_html}</div>'


def _pptx_section_slide(slide: Dict[str, Any]) -> str:
    body = slide.get("body") or slide.get("subtitle")
    body_html = (
        f'<p class="muted" style="font-size:20pt;line-height:1.35;margin-top:0.32in;">{escape(str(body))}</p>'
        if body
        else ""
    )
    return f'<div class="center"><h1>{escape(str(slide.get("title") or "Section"))}</h1>{body_html}</div>'


def _pptx_bullets_slide(slide: Dict[str, Any]) -> str:
    body = slide.get("body")
    body_html = f'<p class="body muted">{escape(str(body))}</p>' if body else ""
    return f"<h2>{escape(str(slide.get('title') or 'Key Points'))}</h2>{body_html}{_bullet_list(slide.get('bullets') or [])}"


def _pptx_two_column_slide(slide: Dict[str, Any]) -> str:
    return (
        f"<h2>{escape(str(slide.get('title') or 'Comparison'))}</h2>"
        '<div class="columns">'
        f"{_card(slide.get('leftTitle') or 'Column 1', slide.get('leftBullets') or [])}"
        f"{_card(slide.get('rightTitle') or 'Column 2', slide.get('rightBullets') or [])}"
        "</div>"
    )


def _pptx_table_slide(slide: Dict[str, Any]) -> str:
    headers = "".join(
        f"<th>{escape(str(header))}</th>" for header in slide.get("headers") or []
    )
    rows = "".join(_table_row_to_html(row) for row in slide.get("rows") or [])
    return f"<h2>{escape(str(slide.get('title') or 'Table'))}</h2><table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"


def _pptx_quote_slide(slide: Dict[str, Any]) -> str:
    quote = escape(str(slide.get("quote") or slide.get("body") or ""))
    attribution = slide.get("attribution")
    attribution_html = (
        f'<p class="muted" style="font-size:16pt;margin-top:0.38in;">- {escape(str(attribution))}</p>'
        if attribution
        else ""
    )
    return f'<div class="center" style="max-width:90%;"><blockquote>"{quote}"</blockquote>{attribution_html}</div>'


def _pptx_summary_slide(slide: Dict[str, Any]) -> str:
    cards = ""
    for index, bullet in enumerate((slide.get("bullets") or [])[:3]):
        cards += (
            '<div class="card">'
            f'<div class="number">{index + 1:02d}</div>'
            f"<p>{escape(str(bullet))}</p>"
            "</div>"
        )
    return f"<h2>{escape(str(slide.get('title') or 'Summary'))}</h2><div class=\"summary\">{cards}</div>"


def _card(title: Any, bullets: List[Any]) -> str:
    return (
        f'<div class="card"><h3>{escape(str(title))}</h3>{_bullet_list(bullets)}</div>'
    )


def _bullet_list(items: List[Any]) -> str:
    return "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in items) + "</ul>"
