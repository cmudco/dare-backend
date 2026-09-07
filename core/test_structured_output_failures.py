from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from core.services.document_enrichment_service import (
    DocumentEnrichmentService,
    EnrichmentTelemetry,
)
from core.services.openai_service import OpenAIService
from core.services.structured_output_error import (
    StructuredOutputError,
    StructuredOutputFailure,
)


class StructuredOutputTests(SimpleTestCase):
    def service(self, content, finish_reason="stop", refusal=None):
        service = object.__new__(OpenAIService)
        service.model = "test-model"
        service._uses_max_completion_tokens = Mock(return_value=False)
        service._apply_openai_sampling = Mock()
        service._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(
                        return_value=SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(
                                        content=content, refusal=refusal
                                    ),
                                    finish_reason=finish_reason,
                                )
                            ],
                            usage=SimpleNamespace(
                                prompt_tokens=20, completion_tokens=10
                            ),
                        )
                    )
                )
            )
        )
        return service

    def test_valid_json_returns_usage(self):
        result, usage = async_to_sync(
            self.service('{"description":"ok"}').generate_structured_output_with_usage
        )([], {})
        self.assertEqual(result, {"description": "ok"})
        self.assertEqual(usage, {"input_tokens": 20, "output_tokens": 10})

    def test_unusable_responses_preserve_usage_without_private_content(self):
        cases = [
            ('{"secret":"private', "stop", None, StructuredOutputFailure.INVALID_JSON),
            ('{"secret":"private', "length", None, StructuredOutputFailure.LENGTH),
            ("", "stop", None, StructuredOutputFailure.EMPTY),
            (None, "stop", "private refusal", StructuredOutputFailure.REFUSAL),
            (None, "content_filter", None, StructuredOutputFailure.FILTERED),
        ]
        for content, finish, refusal, reason in cases:
            with (
                self.subTest(reason=reason),
                self.assertRaises(StructuredOutputError) as caught,
            ):
                async_to_sync(
                    self.service(
                        content, finish, refusal
                    ).generate_structured_output_with_usage
                )([], {})
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(caught.exception.usage["output_tokens"], 10)
            self.assertNotIn("private", str(caught.exception))


class EnrichmentRetryTests(SimpleTestCase):
    @patch("core.services.document_enrichment_service.DocumentEnrichmentCache")
    def test_bounded_retry_bills_each_response_and_never_caches_failure(self, cache):
        for reason in StructuredOutputFailure:
            for recovered in (True, False):
                with self.subTest(reason=reason, recovered=recovered):
                    cache.reset_mock()
                    cache.objects.filter.return_value.first.return_value = None
                    service = DocumentEnrichmentService()
                    usage = {"input_tokens": 20, "output_tokens": 10}
                    failure = StructuredOutputError(reason, usage)
                    result = {"description": "complete"}
                    provider = SimpleNamespace(
                        generate_structured_output_with_usage=AsyncMock(
                            side_effect=[
                                failure,
                                (result, usage) if recovered else failure,
                            ]
                        )
                    )
                    telemetry = EnrichmentTelemetry()
                    with (
                        patch.object(service, "_record_usage") as bill,
                        patch.object(service, "_check_credit") as credit,
                    ):

                        def run():
                            return service._generate_cached(
                                file=SimpleNamespace(id=1, user=SimpleNamespace(id=2)),
                                image=b"image",
                                content_sha256=None,
                                context={},
                                prompt="describe",
                                schema={},
                                route=SimpleNamespace(
                                    model=SimpleNamespace(identifier="test")
                                ),
                                ai_service=provider,
                                output_limit=100,
                                kind="figure_description",
                                telemetry=telemetry,
                            )

                        if recovered and failure.retryable:
                            self.assertEqual(run(), (result, False))
                            cache.objects.update_or_create.assert_called_once()
                        else:
                            with self.assertRaises(StructuredOutputError):
                                run()
                            cache.objects.update_or_create.assert_not_called()
                        attempts = 2 if failure.retryable else 1
                        self.assertEqual(bill.call_count, attempts)
                        self.assertEqual(credit.call_count, attempts)
                        self.assertEqual(telemetry.provider_requests, attempts)
                        limits = [
                            call.kwargs["max_tokens"]
                            for call in provider.generate_structured_output_with_usage.call_args_list
                        ]
                        self.assertEqual(
                            limits,
                            (
                                [100, 200]
                                if reason == StructuredOutputFailure.LENGTH
                                else [100] * attempts
                            ),
                        )

    @patch("core.services.document_enrichment_service.DocumentEnrichmentCache")
    def test_retry_stops_if_remaining_credit_is_insufficient(self, cache):
        cache.objects.filter.return_value.first.return_value = None
        service = DocumentEnrichmentService()
        usage = {"input_tokens": 20, "output_tokens": 10}
        provider = SimpleNamespace(
            generate_structured_output_with_usage=AsyncMock(
                side_effect=StructuredOutputError(StructuredOutputFailure.LENGTH, usage)
            )
        )
        telemetry = EnrichmentTelemetry()
        with (
            patch.object(service, "_record_usage") as bill,
            patch.object(
                service,
                "_check_credit",
                side_effect=[None, ValueError("Insufficient credit")],
            ),
            self.assertRaisesRegex(ValueError, "Insufficient credit"),
        ):
            service._generate_cached(
                file=SimpleNamespace(id=1, user=SimpleNamespace(id=2)),
                image=b"image",
                content_sha256=None,
                context={},
                prompt="describe",
                schema={},
                route=SimpleNamespace(model=SimpleNamespace(identifier="test")),
                ai_service=provider,
                output_limit=100,
                kind="figure_description",
                telemetry=telemetry,
            )
        bill.assert_called_once()
        self.assertEqual(telemetry.provider_requests, 1)
        provider.generate_structured_output_with_usage.assert_awaited_once()
        cache.objects.update_or_create.assert_not_called()
