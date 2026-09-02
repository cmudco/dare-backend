# Non-chat model usage in DARE

This document inventories model calls that happen outside the user's primary
chat-model response. It separates the shared **Background model** setting from
embedding, retrieval, document, media, tool, and maintenance models that use
independent paths.

## Executive summary

The Background model selector controls four text-generation jobs:

1. conversation titles;
2. rolling conversation summaries;
3. memory extraction, including historical memory import; and
4. Advanced RAG query analysis.

It does **not** currently control embeddings, reranking, document vision,
transcription, image generation, MCP web tools, or maintenance commands. Those
layers are described below because they otherwise look like hidden uses of the
selected chat model.

## Complete runtime inventory

| Layer | When it runs | Model source | Credential and routing path | Controlled by Background model? |
| --- | --- | --- | --- | --- |
| Conversation title | After the first message creates a conversation | Shared background model; platform default `gpt-5.6-luna` | `BackgroundModelService`, using the active DARE, BYO, group, public-bot, or LiteLLM wallet route | Yes |
| Rolling conversation summary | After each configured group of completed assistant replies; default group size is five | Shared background model | `BackgroundModelService`; queued on the default worker | Yes |
| Memory extraction | After eligible chat turns and during user-requested historical import | Shared background model | `BackgroundModelService`; queued on the memory worker | Yes |
| Advanced RAG query analysis | Before retrieval when Advanced RAG is enabled; produces intent, keywords, rewrite, and HyDE text | Shared background model | `BackgroundModelService`; falls back to the raw query if analysis fails | Yes |
| Memory embeddings | When a memory statement is stored and when memory retrieval embeds a query | `text-embedding-3-small`, 512 dimensions | Direct OpenAI client using the server OpenAI key | No |
| Document/library embeddings | During chunk ingestion and semantic document queries | `text-embedding-3-large`, normally 3,072 dimensions | Direct OpenAI helper using the server OpenAI key | No |
| Advanced RAG reranking | After candidate retrieval | `BAAI/bge-reranker-v2-m3` by default | Local `sentence-transformers` CrossEncoder | No |
| Document structure parsing | During file ingestion | Docling's local parsing/layout model stack | Local process; OCR is disabled in this path | No |
| Document vision enrichment | For figures and textless PDF pages before chunking | The user's `vision_model` default, overridable per OCR run; candidates come from the active wallet, recommendation first (`DOCUMENT_ENRICHMENT_MODEL` for the catalog, newest Gemini Flash for a proxy) | `core/services/vision_model_service.py`; dispatch and billing follow the chosen model's provider or the LiteLLM proxy | No |
| Audio transcription | When a user submits audio or voice input | `whisper-1`; diarization uses `gpt-4o-transcribe-diarize` | Direct OpenAI transcription service | No |
| Image generation | When the user explicitly requests an image | Catalog-selected `dall-e-2` or `dall-e-3` | OpenAI image-generation service with generation cost metadata | No |
| MCP web search/fetch | When native MCP web tools execute | `claude-haiku-4-5-20251001` | Direct Anthropic server credential in the MCP web service | No |

## The shared Background model path

The four shared jobs call `core/services/background_model_service.py`. The
service resolves both the model and who pays:

- **LiteLLM wallet:** uses the saved `LiteLLMKey.background_model` and the
  proxy's OpenAI-compatible endpoint. Usage belongs to that proxy route.
- **BYO provider key:** uses the user's provider credential directly. DARE
  records the route but does not debit a DARE wallet.
- **DARE or group wallet:** uses the system provider credential and attributes
  usage to the applicable wallet and budget.
- **Public bot:** preserves the bot owner/payer attribution rather than billing
  the anonymous viewer.

The platform fallback is `BACKGROUND_MODEL`, currently defaulting to
`gpt-5.6-luna` in `config/env.py`. A per-call override can replace it for an
internal caller, but the four production call sites above normally share the
same resolver. The background jobs need structured output, which the OpenAI
and proxy services implement; pointing `BACKGROUND_MODEL` at a Claude or
Gemini catalog row fails loudly with `BackgroundModelUnavailable`.

### Shared call sites

- `core/services/conversation_service.py` — first-message title generation,
  capped at 80 characters with `New Chat` as the fallback.
- `conversations/services/summary_service.py` and `conversations/tasks.py` —
  rolling summary generation.
- `memory/services/writer.py` and `memory/tasks.py` — structured memory writer,
  including one repair attempt for malformed structured output.
- `core/services/rag/query_analyzer.py` — structured retrieval-query analysis.

## Embeddings are separate on purpose—but not yet unified in routing

Embeddings convert text into vectors for similarity search; they do not write a
chat response.

### Memory vectors

`memory/services/embeddings.py` uses `text-embedding-3-small` with 512
dimensions. It embeds individual extracted memory statements and retrieval
queries—not entire conversations. If embedding fails, memory retrieval can
still use lexical and recency signals.

This path currently uses the server OpenAI key directly. An embeddings model
exposed by a LiteLLM proxy is **not** selected automatically, even if the proxy
lists one.

### Document and library vectors

`core/helpers/openai.py`, `core/services/document_processor.py`, and the RAG
retrievers use `text-embedding-3-large` for file chunks and document queries.
This is a second direct OpenAI path and is also independent of the Background
model selector.

The two embedding pipelines intentionally have different dimensions and vector
stores, so changing either model requires a compatible re-embedding migration.

## Retrieval layers that do not call a hosted generative model

- `core/services/rag/reranker.py` uses the local
  `BAAI/bge-reranker-v2-m3` CrossEncoder to reorder retrieved candidates.
- BM25 performs lexical scoring with no model API call.
- MMR performs diversity selection with vector math and no additional model.
- Grounding consumes existing retrieval/reranker scores; it does not make a
  second generation call.

## Document ingestion layers

`core/services/document_parsers/docling_parser.py` runs Docling locally to
recover document structure. For content that needs visual interpretation,
`core/services/document_enrichment_service.py` separately invokes the configured
Gemini vision model. The default is `gemini-3.1-flash-lite`, but the service can
choose another visible compatible model if configuration or availability
requires it.

Document vision is wallet-aware, but it is not the same setting as the shared
Background model. This distinction matters: choosing Luna for titles and
memory does not move PDF figure analysis away from Gemini.

## Explicit non-chat modalities

Audio transcription and image generation are user-triggered and visible in the
product, but they are still separate model layers:

- `core/services/whisper_service.py` and
  `conversations/services/audio_transcription_service.py` handle Whisper and
  diarized transcription.
- `core/services/openai_service.py` and
  `conversations/services/image_generation_service.py` handle DALL-E image
  generation and saved-file metadata.

Neither should appear in the Background model shortlist because neither can do
the four shared background text jobs.

## Tool and maintenance-only calls

These calls do not participate in ordinary background processing but still use
models:

- `mcp/services/web_fetch.py` hardcodes Claude Haiku for native web search and
  fetch tools and uses the server Anthropic credential.
- `api_keys/services.py` makes small provider calls only to validate a newly
  entered API key. These are setup probes, not content-generation layers.
- `conversations/management/commands/update_model_card_public_sentiment.py`
  uses `claude-sonnet-4-20250514` in an operator-run maintenance command for
  web research and sentiment updates. It is not part of user request runtime.

## Recommendation policy shown in the LiteLLM UI

The selector recommendations are a presentation shortlist, not another model
call. The backend ranks at most four entries from the proxy's actual model
roster:

1. one canonical GPT-5.6 Luna route; then
2. the newest available Gemini Flash models in descending version order; then
3. other Gemini models if fewer than four recommendations have been filled.

Duplicate Luna aliases are collapsed. GPT-5.6 Sol and Terra are deliberately
excluded from the recommendation box, although every proxy model remains
available in the full dropdown.

## Architectural follow-ups

The main transparency gap is that several direct-provider paths bypass both the
Background model setting and LiteLLM routing. The highest-value consolidation
work would be:

1. make memory and document embedding routes explicit settings with dimensions
   treated as migration-sensitive configuration;
2. expose credential source and cost attribution for every non-chat layer;
3. move MCP web tools and audio transcription through the same dispatch policy
   where provider capabilities allow it; and
4. keep document vision separate by capability, but show its configured model
   beside the Background model in admin diagnostics.
