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
        """Read content from text-based files."""
        with file.file.open('r') as f:
            return f.read()