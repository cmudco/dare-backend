"""
Document parsing configuration

Shared vocabulary for the document-parsing layer: element kinds, the labels a
parser can attach to a text element, and the thresholds that decide whether a
parse actually recovered anything.

Lives in ``core/config`` rather than inside ``document_parsers`` so both the
DTOs and the parsers can import it without a cycle.
"""


class ElementKind:
    """What a document element fundamentally is."""

    TEXT = "text"
    TABLE = "table"
    PICTURE = "picture"


# Skip only confidently decorative figures; size alone does not establish relevance.
PICTURE_SKIP_CONFIDENCE = 0.9
PICTURE_CLASSIFICATION_TOP_K = 5
SKIPPED_PICTURE_CLASSES = frozenset({"logo", "icon", "page_thumbnail"})

# Crop context: heading_context stores candidates because Docling mislabels bylines as headings.
HEADING_CONTEXT_LIMIT = 3
NEIGHBOR_TEXT_LIMIT = 300


class ElementLabel:
    """Semantic role of a text element, mirroring Docling's label vocabulary."""

    TITLE = "title"
    SECTION_HEADER = "section_header"
    TEXT = "text"
    LIST_ITEM = "list_item"
    CAPTION = "caption"
    CODE = "code"
    CODE_OUTPUT = "code_output"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    FOOTNOTE = "footnote"


# Headings, used to build a document outline.
HEADING_LABELS = frozenset({ElementLabel.TITLE, ElementLabel.SECTION_HEADER})

# Running heads, footers and page numbers. Repeated on every page and carrying
# no content, so they are dropped before chunking instead of polluting every
# chunk that straddles a page break.
FURNITURE_LABELS = frozenset({ElementLabel.PAGE_HEADER, ElementLabel.PAGE_FOOTER})

# A page yielding fewer characters than this is treated as having no text at
# all. Scanned pages routinely leak a stray glyph or two from a stamp or a
# margin note; that is not content.
MIN_CHARS_PER_PAGE = 20

# Whole-document floor. A markdown export of a scanned PDF is not empty — it is
# a run of "<!-- image -->" placeholders and empty table pipes — so content is
# measured from the parsed elements, and a document under this many characters
# of real content counts as having recovered nothing.
MIN_CONTENT_CHARS = 40

# Cap on how many elements are persisted in the stored document model. Beyond
# this the outline stops being useful to a human and starts bloating the row;
# the full text is always kept separately in File.extracted_text.
MAX_STORED_ELEMENTS = 2000

# Per-element limits inside the stored document model. The model is an outline,
# not a second copy of the document.
ELEMENT_TEXT_LIMIT = 400
TABLE_MARKDOWN_LIMIT = 4000
CAPTION_LIMIT = 300
SECTION_LIMIT = 120

# Cap on the recorded output kept per notebook code cell. A dataframe dump or a
# progress-bar stream is thousands of characters that say the same thing once.
NOTEBOOK_OUTPUT_LIMIT = 2000
