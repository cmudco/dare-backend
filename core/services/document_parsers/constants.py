"""Constants for the document-parsing layer."""

PARSER_DOCLING = "docling"
PARSER_NOTEBOOK = "notebook"
PARSER_BASIC = "basic"

# Formats routed through Docling in Advanced mode. Advanced means "let
# Docling find the structure", so plain text and CSV go through it too:
# Docling reads .txt as Markdown (headings, lists, tables when present) and
# .csv as a table. JSON has no Docling backend and keeps the basic reader.
DOCLING_EXTENSIONS = frozenset(
    {
        "pdf",
        "md",
        "markdown",
        "txt",
        "text",
        "csv",
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
