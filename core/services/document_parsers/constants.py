"""Constants for the document-parsing layer."""

PARSER_DOCLING = "docling"
PARSER_NOTEBOOK = "notebook"
PARSER_LEGACY = "legacy"

# Formats Docling handles natively. Plain text, markdown, JSON and CSV are
# deliberately absent: they need decoding, not parsing, and the legacy reader
# already handles encoding detection better than a document converter would.
DOCLING_EXTENSIONS = frozenset(
    {
        "pdf",
        "docx",
        "xlsx",
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
