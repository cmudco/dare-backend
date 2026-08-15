#!/usr/bin/env python3
"""Inspect DARE's sample corpus with Docling, without calling a vision model.

This is a local discovery/validation script for the document-enrichment work.
It answers four questions with evidence from the actual files:

1. What structure and page text does Docling recover?
2. Which pictures does its local figure classifier detect?
3. Which pictures survive DARE's proposed size/type/page filters?
4. What context could accompany each crop in a future vision request?

The script never enables Docling picture description and never calls DARE's
LLM service. Classification is local, though the classifier weights may need
to be downloaded by Hugging Face on the first run.

Examples:

    # One mixed text/image PDF, including crops and local classification.
    python scripts/inspect_docling_corpus.py \
        ../../sample_docs/2026-05-ECP-Newsletter.pdf \
        --classify \
        --crops-dir /tmp/docling-crops \
        --output /tmp/docling-newsletter.json

    # All Docling-supported files, structure only (no classifier model).
    python scripts/inspect_docling_corpus.py ../../sample_docs \
        --no-classify --output /tmp/docling-corpus.json
"""

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

SUPPORTED_EXTENSIONS = frozenset({"pdf", "docx", "xlsx", "pptx", "html", "htm", "adoc"})
HEADING_LABELS = frozenset({"title", "section_header"})
FURNITURE_LABELS = frozenset({"page_header", "page_footer", "footnote"})
SKIPPED_PICTURE_CLASSES = frozenset(
    {"logo", "icon", "stamp", "page_thumbnail", "qr_code", "bar_code"}
)
MIN_CHARS_PER_PAGE = 20
MIN_PICTURE_AREA_RATIO = 0.05
NEIGHBOR_TEXT_LIMIT = 300
PROMPT_TEXT_LIMIT = 2000
CLASSIFICATION_TOP_K = 5


@dataclass(frozen=True)
class InspectConfig:
    classify: bool
    crops_dir: Optional[Path]
    min_picture_area_ratio: float
    max_pictures_per_document: Optional[int]


@dataclass(frozen=True)
class ClassificationResult:
    label: str
    confidence: Optional[float]


@dataclass(frozen=True)
class HeadingContext:
    order: int
    page_no: Optional[int]
    text: str


@dataclass(frozen=True)
class ElementSnapshot:
    order: int
    tree_depth: int
    kind: str
    label: str
    page_no: Optional[int]
    text: str
    section: Optional[str]
    caption: Optional[str]
    bbox: Optional[Dict[str, float]]
    raw_item: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class PictureReport:
    order: int
    page_no: Optional[int]
    tree_depth: int
    bbox: Optional[Dict[str, float]]
    area_ratio: Optional[float]
    content_sha256: Optional[str]
    duplicate_of_order: Optional[int]
    page_has_text: Optional[bool]
    section: Optional[str]
    recent_heading_candidates: Tuple[HeadingContext, ...]
    caption: Optional[str]
    previous_text: Optional[str]
    next_text: Optional[str]
    classifications: Tuple[ClassificationResult, ...]
    decision: str
    crop_path: Optional[str]
    vision_prompt_preview: str


@dataclass(frozen=True)
class DocumentReport:
    path: str
    extension: str
    duration_seconds: float
    pages: int
    pages_without_text: int
    page_text_characters: Dict[str, int]
    elements: int
    element_kinds: Dict[str, int]
    headings: int
    tables: int
    pictures: int
    picture_reports: Tuple[PictureReport, ...]
    warnings: Tuple[str, ...]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    default_corpus = Path(__file__).resolve().parents[3] / "sample_docs"
    parser = argparse.ArgumentParser(
        description=(
            "Inspect Docling structure, picture classification, and the context "
            "available for future DARE vision enrichment."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[default_corpus],
        help="Files or directories to inspect (defaults to sample_docs).",
    )
    parser.add_argument(
        "--pattern",
        help="Only include paths whose file name contains this text (case-insensitive).",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        help="Stop after this many matching files.",
    )
    parser.add_argument(
        "--classify",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Run Docling's local DocumentFigureClassifier-v2.5. Disabled by "
            "default so a whole-corpus run stays lightweight."
        ),
    )
    parser.add_argument(
        "--crops-dir",
        type=Path,
        help="Optionally save the exact picture crops seen by the classifier.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the full JSON report here. A compact summary is always printed.",
    )
    parser.add_argument(
        "--min-picture-area",
        type=float,
        default=MIN_PICTURE_AREA_RATIO,
        help="Minimum page-area ratio for a figure to be worth describing.",
    )
    parser.add_argument(
        "--max-pictures-per-document",
        type=int,
        help="Limit detailed picture rows/crops per document (classification still runs).",
    )
    parser.add_argument(
        "--validate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail with exit code 2 if report invariants do not hold.",
    )
    return parser.parse_args(argv)


def discover_files(
    paths: Sequence[Path], pattern: Optional[str], max_files: Optional[int]
) -> List[Path]:
    discovered: List[Path] = []
    pattern_lower = pattern.lower() if pattern else None

    for raw_path in paths:
        path = raw_path.expanduser().resolve()
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            extension = candidate.suffix.lower().lstrip(".")
            if extension not in SUPPORTED_EXTENSIONS:
                continue
            if pattern_lower and pattern_lower not in candidate.name.lower():
                continue
            discovered.append(candidate)

    unique = sorted(set(discovered), key=lambda item: str(item).lower())
    if max_files is not None:
        return unique[: max(max_files, 0)]
    return unique


def build_converter(config: InspectConfig) -> DocumentConverter:
    pdf_options = PdfPipelineOptions()
    pdf_options.do_ocr = False
    pdf_options.do_table_structure = True
    pdf_options.table_structure_options.do_cell_matching = True
    pdf_options.do_picture_description = False
    pdf_options.do_picture_classification = config.classify
    pdf_options.generate_page_images = False
    # Persist crops in the in-memory document when classifying so the report
    # can compute the same content hash a paid-call cache would use.
    pdf_options.generate_picture_images = (
        config.classify or config.crops_dir is not None
    )
    pdf_options.picture_classification_options.engine_options.top_k = (
        CLASSIFICATION_TOP_K
    )

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        }
    )


def label_value(item: Any) -> str:
    return str(getattr(item, "label", "") or "text")


def kind_value(item: Any) -> str:
    class_name = type(item).__name__
    if class_name.startswith("Picture"):
        return "picture"
    if class_name.startswith("Table"):
        return "table"
    return "text"


def provenance(
    item: Any, document: Any
) -> Tuple[Optional[int], Optional[Dict[str, float]]]:
    provenance_items = getattr(item, "prov", None) or []
    if not provenance_items:
        return None, None

    first = provenance_items[0]
    page_no = getattr(first, "page_no", None)
    page = (getattr(document, "pages", None) or {}).get(page_no)
    raw_bbox = getattr(first, "bbox", None)
    if page is None or raw_bbox is None:
        return page_no, None

    try:
        width = page.size.width
        height = page.size.height
        top_left = raw_bbox.to_top_left_origin(page_height=height)
        bbox = {
            "left": round(top_left.l / width, 4),
            "top": round(top_left.t / height, 4),
            "width": round((top_left.r - top_left.l) / width, 4),
            "height": round((top_left.b - top_left.t) / height, 4),
        }
        return page_no, bbox
    except (AttributeError, TypeError, ZeroDivisionError):
        return page_no, None


def caption_text(item: Any, document: Any) -> Optional[str]:
    try:
        caption = item.caption_text(document).strip()
        return caption or None
    except (AttributeError, TypeError):
        return None


def table_text(item: Any, document: Any) -> str:
    try:
        return (item.export_to_markdown(document) or "").strip()
    except (AttributeError, TypeError):
        try:
            return (item.export_to_markdown() or "").strip()
        except (AttributeError, TypeError):
            return ""


def snapshot_elements(document: Any) -> Tuple[ElementSnapshot, ...]:
    snapshots: List[ElementSnapshot] = []
    current_section: Optional[str] = None

    for order, (item, tree_depth) in enumerate(document.iterate_items(), start=1):
        label = label_value(item)
        text = (getattr(item, "text", "") or "").strip()
        kind = kind_value(item)
        if kind == "table":
            text = table_text(item, document)
        if label in HEADING_LABELS and text:
            current_section = text

        page_no, bbox = provenance(item, document)
        snapshots.append(
            ElementSnapshot(
                order=order,
                tree_depth=tree_depth,
                kind=kind,
                label=label,
                page_no=page_no,
                text=text,
                section=current_section,
                caption=caption_text(item, document),
                bbox=bbox,
                raw_item=item,
            )
        )

    return tuple(snapshots)


def page_text_characters(
    document: Any, elements: Sequence[ElementSnapshot]
) -> Dict[int, int]:
    pages = getattr(document, "pages", None) or {}
    characters = {page_no: 0 for page_no in pages}
    for element in elements:
        if element.page_no in characters:
            characters[element.page_no] += len(element.text.strip())
    return characters


def classifications(item: Any) -> Tuple[ClassificationResult, ...]:
    meta = getattr(item, "meta", None)
    classification = getattr(meta, "classification", None)
    predictions = getattr(classification, "predictions", None) or []
    ordered = sorted(
        predictions,
        key=lambda prediction: getattr(prediction, "confidence", 0.0) or 0.0,
        reverse=True,
    )
    return tuple(
        ClassificationResult(
            label=str(getattr(prediction, "class_name", "unknown")),
            confidence=(
                round(float(prediction.confidence), 4)
                if getattr(prediction, "confidence", None) is not None
                else None
            ),
        )
        for prediction in ordered[:CLASSIFICATION_TOP_K]
    )


def nearest_text(
    elements: Sequence[ElementSnapshot],
    picture_index: int,
    direction: int,
    excluded_text: Optional[str],
) -> Optional[str]:
    index = picture_index + direction
    while 0 <= index < len(elements):
        candidate = elements[index]
        if (
            candidate.kind != "picture"
            and candidate.label not in FURNITURE_LABELS
            and candidate.text
            and candidate.text != excluded_text
        ):
            return candidate.text[:NEIGHBOR_TEXT_LIMIT]
        index += direction
    return None


def recent_heading_candidates(
    elements: Sequence[ElementSnapshot], picture_index: int, limit: int = 3
) -> Tuple[HeadingContext, ...]:
    headings: List[HeadingContext] = []
    seen = set()
    for candidate in reversed(elements[:picture_index]):
        if candidate.label not in HEADING_LABELS or not candidate.text:
            continue
        normalized = " ".join(candidate.text.split())
        if normalized in seen:
            continue
        seen.add(normalized)
        headings.append(
            HeadingContext(
                order=candidate.order,
                page_no=candidate.page_no,
                text=normalized,
            )
        )
        if len(headings) >= limit:
            break
    return tuple(reversed(headings))


def picture_decision(
    element: ElementSnapshot,
    page_has_text: Optional[bool],
    prediction_rows: Sequence[ClassificationResult],
    min_area_ratio: float,
    classification_enabled: bool,
) -> str:
    if page_has_text is False:
        return "transcribe_full_page_instead"
    if element.page_no is None or element.bbox is None:
        return "skip_missing_page_position"

    area_ratio = element.bbox["width"] * element.bbox["height"]
    if area_ratio < min_area_ratio:
        return "skip_small_picture"

    if not classification_enabled:
        return "classify_before_decision"
    if not prediction_rows:
        return "classification_missing"

    top_label = prediction_rows[0].label
    if top_label in SKIPPED_PICTURE_CLASSES:
        return f"skip_class:{top_label}"
    return "describe"


def safe_stem(path: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", path.stem).strip("-") or "document"


def crop_image(element: ElementSnapshot, document: Any) -> Optional[Any]:
    try:
        return element.raw_item.get_image(document)
    except AttributeError:
        return None


def image_sha256(image: Optional[Any]) -> Optional[str]:
    if image is None:
        return None

    rgb_image = image.convert("RGB")
    digest = hashlib.sha256(usedforsecurity=False)
    digest.update(f"{rgb_image.width}x{rgb_image.height}:RGB".encode("ascii"))
    digest.update(rgb_image.tobytes())
    return digest.hexdigest()


def save_crop(
    image: Optional[Any], source: Path, order: int, crops_dir: Optional[Path]
) -> Optional[str]:
    if crops_dir is None or image is None:
        return None

    crops_dir.mkdir(parents=True, exist_ok=True)
    crop_path = crops_dir / f"{safe_stem(source)}-order-{order}.jpg"
    image.convert("RGB").save(crop_path, "JPEG", quality=88, optimize=True)
    return str(crop_path)


def vision_prompt_preview(
    source: Path,
    element: ElementSnapshot,
    prediction_rows: Sequence[ClassificationResult],
    heading_candidates: Sequence[HeadingContext],
    previous_text: Optional[str],
    next_text: Optional[str],
) -> str:
    top_class = prediction_rows[0].label if prediction_rows else "unclassified"
    heading_text = " > ".join(row.text for row in heading_candidates)
    context_lines = [
        "Describe this document figure accurately and transcribe any visible text.",
        f"Document: {source.name}",
        f"Page: {element.page_no or 'unknown'}",
        f"Reading-order position: {element.order}",
        f"Local figure class: {top_class}",
        f"Current section: {element.section or 'not detected'}",
        f"Recent heading candidates: {heading_text or 'none'}",
        f"Source caption: {element.caption or 'none'}",
        f"Text immediately before: {previous_text or 'none'}",
        f"Text immediately after: {next_text or 'none'}",
        (
            "Return JSON with description, visible_text, and uncertainty. "
            "Do not infer facts that are not visible in the crop or supplied context."
        ),
    ]
    return "\n".join(context_lines)[:PROMPT_TEXT_LIMIT]


def inspect_document(
    converter: DocumentConverter, source: Path, config: InspectConfig
) -> DocumentReport:
    started = time.time()
    result = converter.convert(source)
    document = result.document
    elements = snapshot_elements(document)
    page_characters = page_text_characters(document, elements)
    warnings: List[str] = []
    picture_rows: List[PictureReport] = []
    candidate_hashes: Dict[str, int] = {}

    pictures = [element for element in elements if element.kind == "picture"]
    selected_pictures = pictures
    if config.max_pictures_per_document is not None:
        selected_pictures = pictures[: max(config.max_pictures_per_document, 0)]
        if len(selected_pictures) < len(pictures):
            warnings.append(
                f"Picture details limited to {len(selected_pictures)} of {len(pictures)}."
            )

    element_indexes = {element.order: index for index, element in enumerate(elements)}
    for element in selected_pictures:
        index = element_indexes[element.order]
        prediction_rows = classifications(element.raw_item)
        image = crop_image(element, document)
        content_sha256 = image_sha256(image)
        page_has_text = (
            page_characters.get(element.page_no, 0) >= MIN_CHARS_PER_PAGE
            if element.page_no is not None
            else None
        )
        area_ratio = (
            round(element.bbox["width"] * element.bbox["height"], 4)
            if element.bbox
            else None
        )
        heading_candidates = recent_heading_candidates(elements, index)
        previous_text = nearest_text(elements, index, -1, element.caption)
        next_text = nearest_text(elements, index, 1, element.caption)
        decision = picture_decision(
            element,
            page_has_text,
            prediction_rows,
            config.min_picture_area_ratio,
            config.classify,
        )
        duplicate_of_order: Optional[int] = None
        if decision == "describe" and content_sha256:
            duplicate_of_order = candidate_hashes.get(content_sha256)
            if duplicate_of_order is not None:
                decision = f"skip_duplicate_of:{duplicate_of_order}"
            else:
                candidate_hashes[content_sha256] = element.order

        picture_rows.append(
            PictureReport(
                order=element.order,
                page_no=element.page_no,
                tree_depth=element.tree_depth,
                bbox=element.bbox,
                area_ratio=area_ratio,
                content_sha256=content_sha256,
                duplicate_of_order=duplicate_of_order,
                page_has_text=page_has_text,
                section=element.section,
                recent_heading_candidates=heading_candidates,
                caption=element.caption,
                previous_text=previous_text,
                next_text=next_text,
                classifications=prediction_rows,
                decision=decision,
                crop_path=save_crop(image, source, element.order, config.crops_dir),
                vision_prompt_preview=vision_prompt_preview(
                    source,
                    element,
                    prediction_rows,
                    heading_candidates,
                    previous_text,
                    next_text,
                ),
            )
        )

    if pictures and not any(element.section for element in pictures):
        warnings.append("No picture was associated with a preceding heading.")
    if pictures and not any(element.caption for element in pictures):
        warnings.append("No picture had a Docling-linked caption.")
    if any(element.tree_depth > 1 for element in pictures):
        warnings.append(
            "Docling returned nested tree depth for at least one picture; DARE does "
            "not currently persist that depth or a full heading path."
        )

    kind_counts = Counter(element.kind for element in elements)
    return DocumentReport(
        path=str(source),
        extension=source.suffix.lower().lstrip("."),
        duration_seconds=round(time.time() - started, 3),
        pages=len(getattr(document, "pages", None) or {}),
        pages_without_text=sum(
            1 for count in page_characters.values() if count < MIN_CHARS_PER_PAGE
        ),
        page_text_characters={
            str(key): value for key, value in page_characters.items()
        },
        elements=len(elements),
        element_kinds=dict(sorted(kind_counts.items())),
        headings=sum(1 for element in elements if element.label in HEADING_LABELS),
        tables=len(getattr(document, "tables", None) or []),
        pictures=len(pictures),
        picture_reports=tuple(picture_rows),
        warnings=tuple(warnings),
    )


def validate_reports(reports: Sequence[DocumentReport], classify: bool) -> List[str]:
    failures: List[str] = []
    for report in reports:
        prefix = Path(report.path).name
        if report.pages_without_text > report.pages:
            failures.append(f"{prefix}: pages_without_text exceeds page count")
        if sum(report.element_kinds.values()) != report.elements:
            failures.append(f"{prefix}: element-kind counts do not reconcile")
        for picture in report.picture_reports:
            if classify and not picture.classifications:
                failures.append(
                    f"{prefix} picture #{picture.order}: classifier returned no prediction"
                )
            if picture.decision == "describe":
                if picture.page_has_text is False:
                    failures.append(
                        f"{prefix} picture #{picture.order}: scan selected for description"
                    )
                if picture.area_ratio is None:
                    failures.append(
                        f"{prefix} picture #{picture.order}: selected without an area"
                    )
    return failures


def summarize(reports: Sequence[DocumentReport]) -> Dict[str, Any]:
    decisions = Counter(
        picture.decision for report in reports for picture in report.picture_reports
    )
    classification_labels = Counter(
        picture.classifications[0].label
        for report in reports
        for picture in report.picture_reports
        if picture.classifications
    )
    pictures_with_section = sum(
        1 for report in reports for picture in report.picture_reports if picture.section
    )
    pictures_with_caption = sum(
        1 for report in reports for picture in report.picture_reports if picture.caption
    )
    detailed_pictures = sum(len(report.picture_reports) for report in reports)
    hashes = Counter(
        picture.content_sha256
        for report in reports
        for picture in report.picture_reports
        if picture.content_sha256
    )
    repeated_hashes = [count for count in hashes.values() if count > 1]
    return {
        "documents": len(reports),
        "pages": sum(report.pages for report in reports),
        "pages_without_text": sum(report.pages_without_text for report in reports),
        "pictures_detected": sum(report.pictures for report in reports),
        "pictures_detailed": detailed_pictures,
        "pictures_with_section": pictures_with_section,
        "pictures_with_caption": pictures_with_caption,
        "pictures_with_content_hash": sum(hashes.values()),
        "exact_duplicate_groups": len(repeated_hashes),
        "exact_duplicate_pictures_beyond_first": sum(
            count - 1 for count in repeated_hashes
        ),
        "decisions": dict(sorted(decisions.items())),
        "top_classifications": dict(classification_labels.most_common()),
    }


def print_summary(reports: Sequence[DocumentReport], summary: Dict[str, Any]) -> None:
    print("\nDocling corpus inspection")
    print("=" * 80)
    for report in reports:
        decisions = Counter(row.decision for row in report.picture_reports)
        print(
            f"{Path(report.path).name}: {report.pages} pages, "
            f"{report.pages_without_text} without text, {report.headings} headings, "
            f"{report.tables} tables, {report.pictures} pictures, "
            f"{report.duration_seconds:.2f}s"
        )
        if decisions:
            print(f"  picture decisions: {dict(sorted(decisions.items()))}")
        for warning in report.warnings:
            print(f"  note: {warning}")

    print("-" * 80)
    print(json.dumps(summary, indent=2, sort_keys=True))


def serialize_report(
    reports: Sequence[DocumentReport],
    summary: Dict[str, Any],
    config: InspectConfig,
    validation_failures: Sequence[str],
) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "docling_version": version("docling"),
        "settings": {
            "classification_enabled": config.classify,
            "classifier": "docling-project/DocumentFigureClassifier-v2.5",
            "picture_description_enabled": False,
            "ocr_enabled": False,
            "min_picture_area_ratio": config.min_picture_area_ratio,
            "skipped_picture_classes": sorted(SKIPPED_PICTURE_CLASSES),
        },
        "summary": summary,
        "validation_failures": list(validation_failures),
        "documents": [asdict(report) for report in reports],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    files = discover_files(args.paths, args.pattern, args.max_files)
    if not files:
        print("No Docling-supported files matched.", file=sys.stderr)
        return 1

    config = InspectConfig(
        classify=args.classify,
        crops_dir=args.crops_dir.resolve() if args.crops_dir else None,
        min_picture_area_ratio=args.min_picture_area,
        max_pictures_per_document=args.max_pictures_per_document,
    )
    converter = build_converter(config)
    reports: List[DocumentReport] = []
    conversion_failures: List[str] = []
    for index, source in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] Inspecting {source.name}...", flush=True)
        try:
            reports.append(inspect_document(converter, source, config))
        except Exception as error:
            conversion_failures.append(
                f"{source.name}: {type(error).__name__}: {error}"
            )
            print(f"  failed: {type(error).__name__}: {error}", file=sys.stderr)

    summary = summarize(reports)
    validation_failures = conversion_failures + validate_reports(
        reports, config.classify
    )
    print_summary(reports, summary)

    payload = serialize_report(reports, summary, config, validation_failures)
    if args.output:
        output_path = args.output.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Full report: {output_path}")

    if validation_failures:
        print("\nValidation failures:", file=sys.stderr)
        for failure in validation_failures:
            print(f"- {failure}", file=sys.stderr)
        return 2 if args.validate else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
