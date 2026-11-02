"""Constants for the document-parsing layer."""

PARSER_DOCLING = "docling"
PARSER_NOTEBOOK = "notebook"
PARSER_BASIC = "basic"

# Formats routed through Docling in Advanced mode. Advanced means "let
# Docling find the structure", so plain text goes through it too: Docling
# reads .txt as Markdown (headings, lists, tables when present). JSON has no
# Docling backend and keeps the basic reader. Spreadsheets skip Docling: they
# are searched by a summary card (see ``table_card``), and Docling's Markdown
# rendering of a large sheet is slow and many times the file's size.
DOCLING_EXTENSIONS = frozenset(
    {
        "pdf",
        "md",
        "markdown",
        "txt",
        "text",
        "docx",
        "pptx",
        "html",
        "htm",
        "adoc",
    }
)

# Jupyter notebooks. Docling does not read them, and the legacy reader would
# decode the raw JSON — cell metadata and base64 image outputs included — so
# they get a parser of their own.
NOTEBOOK_EXTENSIONS = frozenset({"ipynb"})
