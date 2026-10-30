# Coding rules

Follow these rules for new work and any code you touch; an exception needs a concrete reason in the PR.

## 1. Start with the contract

Define the contract before you write the implementation:

- request, response, and socket-event shapes;
- the owner of each field and default;
- authorization and tenant boundaries;
- domain invariants and state transitions;
- failure, retry, and idempotency behavior;
- migrations, rollout, and compatibility needs.

Do not start by adding helpers. Start with the shortest valid data flow.

```text
transport -> validation -> request DTO -> service -> domain -> persistence/integration
                                                               -> response serializer/event
```

Each field is validated, transformed, and defaulted in one named place.

## 2. Keep dependencies one-way

A Django feature may use these layers when they add real value:

```text
<app>/api/          authentication, serializers, thin views
<app>/services/     use-case orchestration, transactions, I/O
<app>/domain/       pure decisions, types, and invariants
<app>/models.py     persistence shape and model-level constraints
<app>/tasks.py      small queue entrypoints that call services
<app>/tests/        unit, contract, integration, and task tests
```

- Views and consumers validate, authorize, call one service, and serialize.
- Services coordinate repositories, providers, transactions, and domain functions.
- Domain code is deterministic and has no ORM, network, queue, or socket access.
- Models describe persisted state; they do not orchestrate workflows.
- Tasks accept stable IDs, reload state, and delegate to a service.
- Lower layers never import API views or transport concerns.
- Do not create every layer mechanically. A layer must own a distinct responsibility.

## 3. One typed request flow

- Build one canonical DTO at the backend boundary.
- Normal sends, regeneration, retries, and background continuations reuse that DTO or an explicit variant.
- Validate and cast once. Downstream code trusts the validated type.
- Do not repeatedly call `str()`, `int()`, `.get()`, or apply fallback defaults to validated fields.
- Prefer frozen dataclasses or Pydantic models over parameter sprawl and `Dict[str, Any]`.
- Do not create pass-through DTOs that merely rename or copy another DTO.
- Keep snake_case internally; the DRF camel-case integration owns wire conversion.

Responses and events must have explicit serializers or schemas. Never make the frontend infer a shape from a generic `result`, JSON string, or union of unrelated payloads.

## 4. One source of truth

- A rule, threshold, mapping, or state transition has one owner.
- Derived state is computed, not stored in parallel representations.
- A list endpoint and its export share one validated query and one service queryset.
- Shared policy used by read and write paths lives in one pure module.
- Compatibility code must be an isolated adapter with a documented removal condition.
- Do not duplicate old and new architectures throughout the feature.
- If a feature has never shipped, remove obsolete schema and code instead of carrying permanent compatibility.

## 5. Persistence and ownership

- Scope every user-owned query by the authenticated user in the service or queryset.
- Cross-user identifiers return 404; unauthenticated access returns 401.
- Use transactions for multi-row invariants and state transitions.
- Make lifecycle states explicit. Do not encode retirement, deletion, and privacy in one ambiguous flag.
- Prefer soft retirement when history or auditability matters; deletion remains an explicit user action.
- Add database constraints and indexes for invariants that must survive concurrency.
- Never add and immediately undo a migration in the same undeployed feature. Replace or squash it when safe.
- Deployed migrations are history: follow them with a new migration and a rollback/data plan.

## 6. LLM and external-service boundaries

- Use the shared provider/service layer so billing, usage, retries, and observability remain intact.
- Use structured output with an explicit schema for machine decisions.
- Treat model output as untrusted input. A deterministic gate owns irreversible policy decisions.
- Keep authorization and security policy outside prompts.
- Regex is acceptable for bounded syntax and known security signals, not broad natural-language classification.
- Every security detector needs adversarial positives and nearby legitimate negatives.
- Record every paid model call, including repair calls, immediately after it succeeds.
- Close async clients in the same event-loop lifecycle that created them.
- Bound retries and make each attempt visible in logs and billing.

## 7. Background work and concurrency

- A job must be safe to retry and must have a persisted idempotency key or completion marker.
- Define the ordering scope explicitly: global, per user, per conversation, or per record key.
- Scale workers only when the ordering invariant remains true under parallel execution.
- Enqueue stable IDs, not large mutable objects.
- Queue configuration must name a real workload. Remove unused queues, workers, and schedulers.
- A worker failure must leave enough persisted evidence to retry or diagnose the turn.

## 8. Errors and defensive code

- Catch specific exceptions at the layer that can add context or recover.
- Do not catch `Exception` and silently choose a default.
- A fallback must be intentional, observable, tested, and behaviorally safe.
- Invalid or impossible states fail at the boundary instead of widening a query or guessing.
- Never log credentials, private payloads, or unredacted provider errors.
- User-facing errors are stable and actionable; logs retain technical context.

## 9. Keep the code small

- Imports belong at module scope. Inline imports require an unavoidable cycle or startup constraint and a short explanation.
- A function operates at one abstraction level and has one reason to change.
- Do not extract a wrapper used once unless it names a real concept or removes branching.
- Add an interface or abstract base only when multiple implementations or a tested boundary exist.
- Delete dead branches, unused constants, stale flags, and obsolete comments while touching their path.
- Do not refactor unrelated areas under a feature commit; record larger cleanup separately.
- Comments explain why, an invariant, or a non-obvious constraint in one or two lines.
- Do not preserve benchmarks, review history, or implementation diaries in source comments.
- Docstrings are for public contracts and non-obvious behavior, not a narration of the code.
- New environment settings should normally be one declaration plus validation where required.

## 10. Tests are part of the feature

Use the cheapest layer that proves the behavior:

- pure unit tests for domain decisions and boundary cases;
- API tests for validation, response shape, 401, 404, and tenant isolation;
- integration tests for ORM constraints, transactions, and serializers;
- task tests for retry, idempotency, and ordering;
- fresh-session end-to-end journeys for socket and LLM behavior.

Further rules:

- Pair every rejection/security case with a legitimate negative case.
- Freeze model-behavior corpora; do not rely only on ad-hoc prompts.
- Automated tests mock paid providers unless the test is explicitly marked as a live integration test.
- Tests must not depend on wall-clock delays, connection age, execution order, or shared seeded state.
- A reply is not proof of persistence. Verify the authoritative database row, audit record, and emitted metadata.
- Regeneration and duplicate delivery require explicit idempotency tests.

## 11. Micro-examples

Each example clarifies a rule; it is not a template to copy.

### Validate once

```python
# Bad: every caller guesses the type and default.
query = str(arguments.get("query", ""))

# Good: the boundary validates; the service receives a string.
serializer.is_valid(raise_exception=True)
service.search(query=serializer.validated_data["query"])
```

### Keep transport thin

```python
def hold(self, request):
    command = HoldMemoryRequest.from_validated(request.data)
    item = memory_service.set_hold(request.user, command)
    return Response(MemorySerializer(item).data)
```

### Fail visibly

```python
# Bad: a provider outage looks like a valid empty result.
except Exception:
    return []

# Good: translate only the failure this layer understands.
except ProviderTimeout as error:
    raise MemoryUnavailable("Provider timed out.") from error
```

### Keep tasks as entrypoints

```python
@job("memory")
def ingest_memory(message_id: int) -> None:
    memory_service.ingest_message(message_id)
```

## 12. Definition of done

A feature is ready only when:

- the request-to-response flow has one clear owner at every step;
- no unused config, migration, queue, flag, endpoint, or compatibility branch was introduced;
- authorization, tenant isolation, failure, and retry paths are tested;
- provider calls are billed and observable;
- formatting, import order, static checks, and relevant tests pass;
- migrations and rollout assumptions are documented;
- the diff contains no unrelated user work;
- comments and docs describe the final design, not how the patch evolved.

Leave touched code cleaner than you found it, but keep cleanup evidence-driven and inside the feature's path.

## Project conventions

- **Formatting.** Black with its default line length (88) and isort. Apply with `black .` and
  `isort .`; check with `black --check .` and `isort -c .`. The repo has no isort config file.
- **Type hints.** Add them to new code, especially in `core/services/` and any module with a
  public surface.
- **Models.** Inherit `BaseModel` from `common/models.py` (`created_at`, `updated_at`,
  `is_active`, `is_deleted`). Most models declare only `active_objects`
  (`ActiveObjectsManager`); reach for `_base_manager` only when you need every row.
- **Model fields.** Add `help_text`, meaningful `related_name` values and a useful `__str__`.
- **Constants.** Put them in the app's `constants.py` as `TextChoices` or `IntegerChoices`.
- **Translation.** Wrap user-facing strings with `gettext_lazy as _`. Do not translate logged
  exceptions or developer-facing error messages.
- **REST views.** Use `viewsets.ModelViewSet` for standard CRUD. Always declare
  `permission_classes`, defaulting to `[IsAuthenticated]`.
- **Serializers.** Keep read and write fields separate; casing rules are in
  [serialization.md](serialization.md).
- **Background jobs.** Send blocking, slow or external work through Django RQ with
  `@job("<queue>")` on a queue defined in `RQ_QUEUES` (`config/settings/common.py`). Log enough to
  diagnose a failure from `python manage.py rqstats` and the worker log.
- **Tests.** Use `TestCase` for database tests and `SimpleTestCase` otherwise. Mock LLM APIs,
  vector stores and email.
- **Mermaid.** Use alphanumeric node IDs, quote labels that contain punctuation, and prefix
  reserved words (`node_end`).
