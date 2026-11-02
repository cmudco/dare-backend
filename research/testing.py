"""Test helpers for research mode."""

import shutil
import tempfile
from pathlib import Path

from django.test import override_settings


class TempProfilesMixin:
    """Point Hermes profiles at a throwaway directory for the test."""

    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        override = override_settings(
            HERMES_PROFILES_ROOT=str(self.root), HERMES_TOOLS_WAIT_SECONDS=0
        )
        override.enable()
        self.addCleanup(override.disable)
