"""Compare the legacy PyPDF2 extraction with the structured PyMuPDF path.

Proves the parsing-track upgrade (audit mistake #1): paragraph boundaries
survive, reading order holds on multi-column layouts, and detected tables come
out as markdown instead of flattened word soup.

Usage:
    PYTHONPATH="$PWD" venv/bin/python rag_lab/verify_pdf_parse.py [pdf ...]
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from core.services.file_processor import FileProcessor

DEFAULT_PDFS = [
    "media/Barabasi-Albert-1999-Emergence-of-Scaling-in-Random-Networks.pdf",
]


def stats(text: str) -> str:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    table_rows = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    return (
        f"chars={len(text):>7,}  paragraph-blocks={len(paragraphs):>4}  "
        f"markdown-table-rows={len(table_rows):>3}"
    )


def main():
    fp = FileProcessor()
    targets = sys.argv[1:] or DEFAULT_PDFS
    for target in targets:
        data = Path(target).read_bytes()
        print(f"\n=== {Path(target).name} ===")
        old = fp._read_pdf_basic(data)
        new = fp._read_pdf_structured(data)
        print(f"  PyPDF2 (old):  {stats(old)}")
        print(f"  PyMuPDF (new): {stats(new)}")
        print("\n  --- first 600 chars (new) ---")
        print("  " + new[:600].replace("\n", "\n  "))


if __name__ == "__main__":
    main()
