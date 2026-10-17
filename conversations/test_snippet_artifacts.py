from django.test import SimpleTestCase

from conversations.constants import ArtifactType
from conversations.domain.snippet_artifacts import snippet_artifact_shape


class SnippetArtifactShapeTests(SimpleTestCase):
    def test_jsx_component_is_named_after_its_default_export(self):
        shape = snippet_artifact_shape(
            "jsx", "export default function Counter() { return <div /> }"
        )
        self.assertEqual(shape.artifact_type, ArtifactType.REACT)
        self.assertEqual(shape.title, "Counter")
        self.assertEqual(shape.filename, "Counter.jsx")

    def test_javascript_importing_react_is_a_component(self):
        shape = snippet_artifact_shape(
            "javascript", "import { useState } from 'react'\nexport default App"
        )
        self.assertEqual(shape.artifact_type, ArtifactType.REACT)
        self.assertEqual(shape.filename, "snippet.jsx")

    def test_plain_javascript_stays_code(self):
        shape = snippet_artifact_shape("js", "console.log('react')")
        self.assertEqual(shape.artifact_type, ArtifactType.CODE)
        self.assertEqual(shape.title, "JavaScript snippet")

    def test_names_come_from_html_title_and_leading_comment(self):
        html = snippet_artifact_shape(
            "html", "<html><head><title>Click Counter</title></head></html>"
        )
        self.assertEqual(html.title, "Click Counter")
        self.assertEqual(html.filename, "click_counter.html")
        python = snippet_artifact_shape("python", "# List slicing demo\nx = [1]")
        self.assertEqual(python.title, "List slicing demo")
        self.assertEqual(python.filename, "list_slicing_demo.py")

    def test_shebangs_and_preprocessor_lines_are_not_titles(self):
        self.assertEqual(
            snippet_artifact_shape("bash", "#!/bin/bash\necho hi").title,
            "Bash snippet",
        )
        self.assertEqual(
            snippet_artifact_shape("c", "#include <stdio.h>").title, "C snippet"
        )

    def test_mermaid_becomes_a_diagram(self):
        shape = snippet_artifact_shape("mermaid", "flowchart LR\n  A --> B")
        self.assertEqual(shape.artifact_type, ArtifactType.DIAGRAM)
        self.assertEqual(shape.title, "Diagram snippet")
        self.assertEqual(shape.content_type, "text/mermaid")

    def test_html_and_unknown_languages(self):
        html = snippet_artifact_shape("HTML", "<p>hi</p>")
        self.assertEqual(html.artifact_type, ArtifactType.HTML)
        self.assertEqual(html.title, "HTML snippet")
        unknown = snippet_artifact_shape("", "plain text")
        self.assertEqual(unknown.artifact_type, ArtifactType.CODE)
        self.assertEqual(unknown.filename, "snippet.txt")
        self.assertEqual(unknown.language, "text")
