import io
import PyPDF2
from typing import Dict, List, Any
from files.models import File

class FileProcessor:
    """Service for processing different types of files."""

    def read_file_content(self, file: File) -> str:
        """Read and extract content from various file types"""
        try:
            file_name = file.file.name.lower()

            if file_name.endswith('.pdf'):
                return self._read_pdf(file)
            elif file_name.endswith(('.txt', '.md', '.json')):
                return self._read_text_file(file)
            else:
                return f"File: {file.name or file.file.name}"

        except Exception as e:
            raise Exception(f"Error reading file content: {str(e)}")

    def _read_pdf(self, file: File) -> str:
        """Extract text from PDF file."""
        with file.file.open('rb') as f:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(f.read()))
            text_content = []
            for page in pdf_reader.pages:
                text_content.append(page.extract_text())
            return ' '.join(text_content)

    def _read_text_file(self, file: File) -> str:
        """Read content from text-based files with encoding detection."""
        # Read file as binary first
        with file.file.open('rb') as f:
            raw_content = f.read()

        # List of encodings to try in order of preference
        encodings_to_try = [
            'utf-8',
            'utf-8-sig',  # UTF-8 with BOM
            'latin-1',    # ISO-8859-1
            'cp1252',     # Windows-1252
            'iso-8859-1', # Latin-1
            'ascii',
        ]

        # Try different encodings on the binary content
        for encoding in encodings_to_try:
            try:
                content = raw_content.decode(encoding)
                return content
            except UnicodeDecodeError:
                continue
            except Exception as e:
                continue

        # Final fallback: decode with error handling
        try:
            content = raw_content.decode('utf-8', errors='replace')
            return content
        except Exception as final_error:
            raise Exception(f"Could not decode text file with any encoding method: {str(final_error)}")