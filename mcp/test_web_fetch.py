from unittest.mock import patch

from django.test import SimpleTestCase

from mcp.services.web_fetch import _direct_pdf_text, _is_public_url


def _resolves_to(address):
    return patch(
        "mcp.services.web_fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", (address, 443))],
    )


class DirectPdfGuardTests(SimpleTestCase):
    def test_internal_addresses_are_never_fetched(self):
        for address in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"):
            with self.subTest(address=address), _resolves_to(address):
                self.assertFalse(_is_public_url("https://example.org/report.pdf"))

    def test_a_public_address_is_allowed(self):
        with _resolves_to("93.184.215.14"):
            self.assertTrue(_is_public_url("https://example.org/report.pdf"))

    def test_a_host_resolving_inside_is_not_downloaded(self):
        with _resolves_to("127.0.0.1"), patch(
            "mcp.services.web_fetch.httpx.stream"
        ) as stream:
            self.assertEqual(_direct_pdf_text("https://example.org/a.pdf"), "")
        stream.assert_not_called()
