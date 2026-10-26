from django.test import TestCase

from release_notes.models import ReleaseNote


class CleanReleaseNotesTestCase(TestCase):
    """Starts each test class without the releases that data migrations seed."""

    @classmethod
    def setUpTestData(cls):
        ReleaseNote.objects.all().delete()
