"""Constants for the document-parsing layer."""

PARSER_DOCLING = "docling"
PARSER_NOTEBOOK = "notebook"
PARSER_BASIC = "basic"

# Formats routed through Docling in Advanced mode. Markdown has explicit
# headings, lists and tables; plain text, JSON and CSV retain the basic reader.
DOCLING_EXTENSIONS = frozenset(
    {
        "pdf",
        "md",
        "markdown",
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
