from django.test import TestCase

from files.models import File
from files.services.source_size import count_tokens, estimate_source_size
from users.models import User


class SourceSizeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="sizes@example.test", password="x")

    def make_file(self, name, *, text=None, size=0):
        file = File(user=self.user, file=f"files/{name}", name=name, size=size)
        file.set_extracted_text(text)
        file.save()
        return file

    def test_extracted_text_stores_its_token_count(self):
        file = self.make_file("notes.txt", text="The river carries sediment.")

        self.assertEqual(file.token_count, count_tokens("The river carries sediment."))

    def test_size_sums_stored_counts_and_estimates_unparsed_files(self):
        parsed = self.make_file("parsed.txt", text="one two three")
        self.make_file("pending.pdf", size=4000)

        size = estimate_source_size(File.active_objects.filter(user=self.user))

        self.assertEqual(size.file_count, 2)
        self.assertEqual(size.estimated_tokens, parsed.token_count + 1000)
