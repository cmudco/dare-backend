# DARE LLM Gateway User Guide

## Table of Contents

Introduction

Navigation Overview

Dashboard

Conversations

Configuration Panel

Selecting Models

Files

Using Documents in Conversations

Prompts

Agents

Workflows

Integrations

Memory

Billing and Cost Tracking

Group Wallets

Learning Progression

Troubleshooting & Support

Conclusion

## Introduction

DARE (Dietrich Analysis Research Education) is Carnegie Mellon University's LLM gateway for research, teaching, learning, and experimentation with large language models. The platform gives you access to multiple AI providers through one interface while preserving the transparency and control needed for academic and professional work.

Unlike commercial LLM platforms that often hide how outputs are produced, DARE is designed to make the working process visible. You can choose models, adjust model behavior, attach files, reference earlier conversations, use prompts and agents, build workflows, connect approved tools, track cost, and review generated artifacts.

This guide follows the layout of the DARE interface. It is written from the perspective of a user working inside the application, so each section explains what you will see, what each feature is for, and when to use it.

Some features described in this guide appear only when they are enabled for your account, group, or deployment. If you do not see a feature such as Integrations, Memory, Artifacts, Audio Transcription, Voice Input, Bring Your Own Key, or Group Wallet, your account may not have access to that feature yet.

## Navigation Overview

### Left Sidebar Menu

Dashboard: Overview of your usage statistics, recent activity, wallet balance, and energy-related metrics.

Conversations: Your chat sessions with AI models, including model selection, file context, prompts, agents, tools, artifacts, and references.

Files: Document and media management, including upload, tagging, folders, sharing, processing status, and document use in conversations.

Prompts: Your prompt templates, shared prompts, and prompt library content.

Workflows: Multi-step AI processes built on a visual canvas.

Agents: Reusable agent templates you can apply in conversations and workflows.

Integrations: Model Context Protocol (MCP) servers and tools, if enabled for your account.

Memory: Cross-conversation memory that DARE can recall in future conversations, if enabled for your account.

Cost Tracking: Your wallet balance, available billing options, and transaction history.

Group Wallet: Budget and allocation controls for group owners, if you manage a wallet-enabled group.

Help: Support resources, model information, and guidance.

Settings: Account configuration, profile settings, API key settings when available, and platform preferences.

### Header and Global Controls

The top header includes account access, notifications, theme controls, and system-level messages. System banners may appear when administrators need to communicate platform status, service notices, or deployment-specific guidance.

The sidebar can collapse on smaller screens. On mobile or narrow browser windows, some navigation items may appear as icons only.

# Dashboard

The Dashboard is your starting point. It summarizes your activity, platform usage, cost information, and model impact metrics.

## Usage Statistics Panels

### Top Row

AI Messages: Total AI-generated responses.

Conversations: Total conversations started.

Files: Total files uploaded to the platform.

### Middle Row

Messages: Total exchanges between you and AI models.

Prompts: Total prompts created or available in your prompt workspace.

Tagged Files: Files that have organizational tags.

### Bottom Row

Input Tokens: Tokens used in your messages and context.

Output Tokens: Tokens generated in AI responses.

Total Tokens: Combined input and output token usage.

### Wallet Balance

Your current available credit appears on the Dashboard and in Cost Tracking. This helps you monitor whether you have enough balance to continue using DARE-hosted model access.

If your account can use more than one billing source, the visible wallet balance may represent your currently selected wallet or the DARE wallet summary, depending on the page. Use Cost Tracking to review all available wallet options.

## Activity Summary

Message Ratio: Percentage and balance of user and AI messages.

Files per Conversation: Average number of files used across conversations.

Last Updated: Timestamp showing when dashboard metrics were refreshed.

## Energy and Environmental Metrics

DARE may show energy and environmental impact estimates for model usage. These estimates help you understand the relative cost and sustainability profile of different model choices.

You may see:

- Energy usage summaries.
- Model breakdown charts.
- Provider or tier comparisons.
- Relatable impact statistics.

Use these metrics as guidance when choosing models. Larger or more capable models may be useful for complex tasks, but smaller models can be more efficient for quick questions, formatting, classification, or lightweight drafting.

# Conversations

The Conversations section is where you interact with AI models through a chat-like interface. Conversations can include text, file context, prompts, agents, references to other conversations, memory, MCP tools, and generated artifacts.

## Starting a New Conversation

Click Conversations in the sidebar.

Use the message field at the bottom of the screen.

Choose a model from the model selector.

Adjust configuration options if needed.

Type your message and press Enter or click Send.

If you open an existing conversation from the conversation history, DARE continues that conversation with its saved settings and context.

## Conversation Interface Elements

### Central Chat Area

The central chat area displays the back-and-forth messages between you and the AI.

It may show:

- AI responses streamed in real time.
- Citations or document references when files are used.
- Web search or web fetch sources when enabled.
- Memory sources when memory is used.
- Tool call indicators when MCP or DARE tools are used.
- Artifact cards for generated charts, diagrams, documents, slides, or interactive components.
- Cost, model, and energy metadata where available.

### Right Sidebar

The right sidebar lists your saved conversations for quick access.

Depending on your account and the current interface state, it may show:

- Conversation titles.
- Search and filtering controls.
- Favorite or summary indicators.
- Sharing actions when sharing is enabled.
- Collapse controls.

When the artifact sidecar is open, the conversation history may collapse or hide to give more space to the artifact preview.

### Bottom Controls

Message Input Field: Type your message here.

Model Selector: Choose which model to use.

File Button: Add uploaded documents, media, folders, or tags to the conversation context.

Prompt Button: Select a saved prompt or agent template.

Reference Conversations Button: Include context from other conversations.

MCP Server Selector: Select connected MCP servers for tool use, if Integrations are enabled.

Settings Button: Open the conversation-specific configuration panel.

Voice Input Button: Record spoken input, if enabled for your account.

Image or Media Controls: Upload images or audio files when supported by the selected model and enabled features.

## Reference Conversations Feature

The Reference Conversations feature allows you to incorporate context from saved conversations into a new or existing conversation.

### Accessing Reference Conversations

Click the reference conversation icon near the model picker and configuration controls.

A Reference Conversations panel appears.

Search or browse your conversation history.

Select conversations you want DARE to use as additional context.

### How Reference Conversations Work

Search Conversations: Filter your conversation history by title or content.

Select Conversations: Check the conversations you want to reference.

Reference Summaries: When summaries are available, DARE can use a compact summary instead of requiring the full conversation text.

Context Integration: Selected conversations help the AI understand prior work, decisions, terminology, and direction.

Continuity: Referenced conversations help maintain continuity across separate sessions.

### Memory Option

If Memory is enabled for your account, the Reference Conversations panel includes a Memory toggle. Turning this on allows DARE to search relevant cross-conversation memories and include them in the current response.

Use Memory when you want DARE to remember recurring preferences, research interests, project context, or facts that should carry across conversations.

Keep Memory off when a conversation should remain isolated or when you do not want prior preferences to influence the response.

### Use Cases for Reference Conversations

#### Experimental Branching

Reference a successful conversation while testing a new approach.

Compare how different models handle the same underlying context.

Build on a previous answer while exploring alternative directions.

#### Cross-Conversation Learning

Reference conversations where you tested a prompt, workflow, method, or model.

Apply successful strategies from one project to another.

Build cumulative knowledge across related conversation sessions.

#### Iterative Development

Reference earlier drafts while revising a document, code plan, lesson, or analysis.

Test different system prompts while preserving awareness of previous attempts.

Compare results across different models or configurations.

#### Knowledge Transfer

Reference related projects, papers, meetings, or experiments.

Apply insights from one analysis to a similar problem.

Maintain consistency across related work.

### Benefits of Reference Conversations

Continuity: Maintain context across multiple conversation sessions.

Experimentation: Try variations without losing previous successful work.

Learning: Build on prior insights and examples.

Efficiency: Avoid re-explaining context that already exists in DARE.

## Conversation Management

Rename conversations by editing the title.

Clone conversations to copy a useful setup or continue from a prior state.

Delete conversations you no longer need.

Favorite important conversations when the option is available.

Share conversations with other users or with your group when sharing is enabled.

Export conversations to PDF when export is available.

Use dark mode or light mode based on your viewing preference.

# Configuration Panel

The Configuration Panel allows you to customize AI behavior and capabilities for an individual conversation. Each conversation can have its own settings, and changes are remembered when you return.

## Accessing the Configuration Panel

Open any conversation.

Click the settings button near the message input controls.

The Configuration Panel opens as a popover.

Adjust the available settings for that conversation.

If a setting is not visible, it may not be supported by the selected model, selected wallet, or your account configuration.

## Configuration Options

### Web Search

Toggle: ON/OFF

Description: Enables real-time web search for current information.

When Enabled:

- The AI can search the web for current information.
- Results can be incorporated into responses.
- Sources may appear below the response.
- Useful for news, recent events, current policies, market changes, or newly released software information.

When to Enable:

- Questions about current events or recent developments.
- Queries requiring up-to-date information.
- Research that needs verification against current sources.
- Topics that change frequently.

When to Disable:

- Conversations using only uploaded documents.
- Historical or timeless topics.
- Tasks where you want the model to rely only on provided context.
- Cost-sensitive or latency-sensitive work.

Default: OFF

### Web Fetch

Toggle: ON/OFF

Description: Lets supported providers fetch explicit URLs and PDFs that you paste into the conversation.

Web Fetch appears when the selected provider supports provider-native URL or PDF retrieval, such as Claude or Gemini models configured for this capability.

When Enabled:

- You can paste a URL or PDF link and ask the model to use it.
- The provider can retrieve the linked content directly.
- This is useful for research papers, source pages, arXiv links, and public PDFs.

When to Use Web Fetch Instead of Web Search:

- You already know the exact page or PDF you want the model to read.
- You need analysis of a specific source rather than general search results.
- You want the response grounded in a known document.

When to Disable:

- You are not using explicit URLs.
- You want to rely only on uploaded files or conversation context.
- The selected wallet or provider does not support this capability.

Default: OFF

### Image Generation

Toggle: ON/OFF

Description: Enables AI image generation when supported and enabled for your account.

When Enabled:

- DARE can select or use an image generation model.
- You can describe the image you want to create.
- Generated images appear in the conversation.
- Image settings may allow size, quality, and style adjustments.

When to Enable:

- Creative projects requiring visual content.
- Illustrations, concept art, mockups, or classroom visuals.
- Visual brainstorming.
- Generating assets for drafts or examples.

When to Disable:

- Text-only conversations.
- Work that should not use image generation.
- Cost-sensitive tasks.

Default: OFF

### Audio Transcription

Toggle: ON/OFF

Description: Converts audio files to text using supported transcription models when enabled for your account.

When Enabled:

- You can upload audio through the conversation media controls.
- DARE can transcribe audio into text.
- Supported transcription models may include Whisper or Gemini-based audio models, depending on your deployment.

When to Enable:

- Transcribing interviews, lectures, notes, or spoken reflections.
- Turning meeting audio into a summary.
- Preparing audio content for analysis.

When to Disable:

- The conversation does not include audio.
- You need to avoid extra model cost.
- Your selected model or wallet does not support transcription.

Default: OFF

### Voice Input

Voice Input may appear as a microphone or push-to-talk control when enabled for your account.

Use Voice Input when you want to speak a prompt instead of typing it. DARE transcribes the recording and sends the transcribed text into the conversation.

Voice Input is most useful for quick brainstorming, verbal notes, accessibility support, and hands-free drafting. Review the transcription before relying on it for precise technical or quoted material.

### Artifacts

Toggle: ON/OFF

Description: Enables long-form and structured artifact generation with a side panel.

When Enabled:

- DARE can create rich artifacts instead of placing everything in a normal chat bubble.
- Artifacts can include charts, diagrams, Word-style documents, PowerPoint-style presentations, React components, code, markdown, images, and other structured outputs.
- Artifact cards appear in the conversation.
- Clicking an artifact opens it in the artifact sidecar.
- Some artifacts can be copied, downloaded, exported, or updated.

When to Enable:

- Creating a chart, diagram, report, guide, slide deck, document, or interactive component.
- Working on long-form content that benefits from a preview panel.
- Iterating on an artifact across multiple versions.

When to Disable:

- Short question-answer conversations.
- Tasks where plain text is sufficient.
- Situations where your selected model or wallet does not support tool-based artifact generation.

Default: OFF

### Temperature

Slider Range: Precise -> Balanced -> Creative

Numeric Range: 0.0 to 1.0

Default: 0.7

What It Controls: Temperature controls randomness and variation in AI responses. Lower values produce more focused, consistent outputs. Higher values produce more varied and creative responses.

Precise (0.0 - 0.4):

- More deterministic responses.
- Better for extraction, formatting, classification, and technical precision.
- Less variation between similar prompts.

Balanced (0.4 - 0.7):

- Good default for general conversation and analysis.
- Balances accuracy with readable variation.
- Useful for most research, writing, and summarization tasks.

Creative (0.7 - 1.0):

- More exploratory and varied.
- Useful for brainstorming, creative writing, and ideation.
- May be less consistent for factual or structured tasks.

Adjustment Tips:

- Start with 0.7 for general use.
- Lower temperature for exact formatting, coding, extraction, or high-stakes analysis.
- Raise temperature for brainstorming or creative alternatives.
- Some models do not expose temperature controls; when unsupported, DARE hides the setting.

### Effort

Control: Button group, when supported by the selected model.

Description: Controls reasoning depth for models that support provider-native effort settings.

When Available:

- Effort appears only for models that expose this capability.
- Higher effort can improve complex reasoning, planning, or multi-step analysis.
- Lower effort can reduce latency and cost for simple tasks.

Use Higher Effort For:

- Difficult reasoning.
- Planning.
- Complex code or data analysis.
- Multi-step evaluation.
- Tasks where correctness is more important than speed.

Use Lower Effort For:

- Quick summaries.
- Simple rewriting.
- Formatting.
- Short answers.
- Low-risk drafts.

### Max Tokens

Slider Range: 1 to 16,384 tokens

Default: 4,096 tokens

What It Controls: Sets the maximum length of an AI response. One token is roughly 3-4 characters in English text.

Token Guidelines:

- 500-1,000 tokens: Short responses, brief summaries, compact answers.
- 1,500-2,500 tokens: Standard responses and detailed explanations.
- 3,000-4,096 tokens: Longer analysis, drafts, and structured responses.
- 4,096+ tokens: Long-form content, reports, artifact generation, or extended reasoning.

Considerations:

- Higher values can increase token usage and cost.
- Responses may be shorter than the maximum if complete.
- If responses are cut off, increase this value.
- If responses are too long or expensive, decrease this value.

### History Limit

Slider Range: Minimal -> Standard -> Max

Numeric Range: 1 to 50 messages

Full Context: Displayed when the slider reaches the maximum.

Default: 20

What It Controls: Determines how many previous messages from the conversation are included as context for each new response.

Minimal:

- Recent exchanges only.
- Lower token usage.
- Faster processing.
- Useful when each question stands alone.

Standard:

- Balanced context retention.
- Good for most iterative conversations.
- Preserves recent discussion without excessive context.

Max or Full Context:

- Includes a large amount of prior conversation history.
- Useful for long research sessions or multi-step work.
- Higher token usage and greater chance of hitting model limits.

Adjustment Tips:

- Increase if the AI seems to forget recent context.
- Decrease if each prompt is independent.
- Use Reference Conversations or Memory for cross-conversation context instead of always increasing the history limit.

## Configuration Management

### Conversation-Specific Settings

All configuration changes are saved to the active conversation.

Settings persist when you close and reopen the conversation.

Each conversation can maintain a different setup.

No manual save action is required.

### Reset to Defaults Button

The Reset to Defaults button returns the active conversation to standard settings.

Default values include:

- Web Search: OFF
- Web Fetch: OFF
- Image Generation: OFF
- Audio Transcription: OFF
- Artifacts: OFF
- Temperature: 0.7
- Effort: model default or unset
- Max Tokens: 4,096
- History Limit: 20

Use Reset to Defaults when you have been experimenting with settings and want a clean baseline.

### Global vs. Conversation Settings

Conversation-Level Settings:

- Temperature
- Effort
- Max Tokens
- History Limit
- Web Search
- Web Fetch
- Image Generation
- Audio Transcription
- Artifacts
- Selected model
- Selected wallet capabilities

Account or System-Level Settings:

- Profile information
- Theme preferences
- API key settings when available
- Account access and group membership
- Feature availability
- Billing and wallet access

Best Practice: Configure each conversation for its specific task. A creative brainstorming conversation and a precise document analysis conversation should usually use different settings.

## Configuration Strategies by Use Case

### Research and Analysis

Recommended Settings:

- Web Search: ON when current information is needed.
- Web Fetch: ON when analyzing specific URLs or PDFs.
- Image Generation: OFF unless visuals are needed.
- Audio Transcription: OFF unless using audio sources.
- Artifacts: ON for reports, charts, diagrams, or structured outputs.
- Temperature: 0.4-0.6.
- Max Tokens: 3,000-6,000.
- History Limit: 30-50 for sustained research.

Rationale: Research benefits from accuracy, source grounding, and enough room for explanation.

### Creative Writing

Recommended Settings:

- Web Search: OFF unless researching current or factual details.
- Image Generation: ON if generating cover art or illustrations.
- Artifacts: ON for long-form drafts.
- Temperature: 0.7-0.9.
- Max Tokens: 3,000-6,000.
- History Limit: 30-50 for story continuity.

Rationale: Creative work benefits from higher variation and longer outputs.

### Technical Documentation

Recommended Settings:

- Web Search: ON only when verifying current syntax or version-specific details.
- Web Fetch: ON for specific docs pages or source URLs.
- Artifacts: ON for polished guides or structured documentation.
- Temperature: 0.2-0.5.
- Max Tokens: 2,000-5,000.
- History Limit: 20-40.

Rationale: Technical documentation requires consistency, careful formatting, and controlled language.

### Quick Q&A

Recommended Settings:

- Web Search: OFF unless the answer depends on recent information.
- Artifacts: OFF.
- Temperature: 0.5-0.7.
- Max Tokens: 500-1,500.
- History Limit: 5-20.

Rationale: Quick questions are usually faster and cheaper with compact settings.

### Brainstorming

Recommended Settings:

- Web Search: OFF unless ideas depend on current data.
- Image Generation: ON if visual exploration is useful.
- Artifacts: ON for structured idea boards, diagrams, or slide drafts.
- Temperature: 0.7-1.0.
- Max Tokens: 2,000-4,000.
- History Limit: 20-30.

Rationale: Brainstorming benefits from variety, but still needs enough context to stay relevant.

### Data Analysis

Recommended Settings:

- Web Search: OFF unless retrieving current data.
- Web Fetch: ON for specific datasets or public reports.
- Artifacts: ON for charts, tables, and reports.
- Temperature: 0.1-0.4.
- Max Tokens: 2,000-5,000.
- History Limit: 20-40.

Rationale: Data analysis benefits from precision and structured output.

## Troubleshooting Configuration Issues

### Problem: Responses are inconsistent or random

Lower the temperature.

Use a more precise model if available.

Provide clearer instructions and examples.

Reduce conflicting context from references, files, or memory.

### Problem: Responses feel repetitive or rigid

Raise the temperature slightly.

Ask for multiple alternatives.

Use a model better suited to creative or exploratory writing.

### Problem: Responses are cut off mid-sentence

Increase Max Tokens.

Ask for a shorter answer or a specific section at a time.

Use artifact mode for longer documents.

### Problem: AI forgets earlier conversation context

Increase History Limit.

Reference the relevant conversation.

Turn on Memory if enabled and appropriate.

Summarize the key context in your new prompt.

### Problem: Responses are too brief

Increase Max Tokens.

Ask for more detail, examples, or structure.

Use a model better suited for long-form output.

### Problem: High token usage and costs

Lower Max Tokens.

Lower History Limit.

Remove unnecessary file context.

Use a smaller or lower-cost model where appropriate.

Disable Web Search, Web Fetch, Artifacts, or Audio Transcription when not needed.

### Problem: Web Search is not providing current information

Confirm Web Search is enabled.

Ask for current sources explicitly.

Use precise search terms.

If you have a specific source, use Web Fetch or upload the file instead.

### Problem: Web search not providing current information

This is the same issue as the Web Search troubleshooting item above. The usual fix is to confirm Web Search is enabled, ask for current sources directly, or use Web Fetch when you already have a specific URL.

### Problem: Web Fetch is not using a URL or PDF

Confirm Web Fetch is visible and enabled.

Use a supported provider.

Paste the full URL directly in the message.

If the URL is private or blocked, upload the file to DARE instead.

### Problem: Image Generation is not working

Confirm Image Generation is enabled for your account.

Confirm the selected model or auto-selected image model is available.

Check your wallet or billing source.

Simplify the image request if the model rejects the prompt.

### Problem: Image generation not working

This is the same issue as the Image Generation troubleshooting item above. Confirm the feature is enabled for your account, check the selected model and wallet, and simplify the request if the model or policy rejects it.

### Problem: Artifacts are not appearing

Confirm Artifacts is enabled in the Configuration Panel.

Ask for a concrete artifact type, such as "create a chart", "make a DOCX-style report", or "generate a slide deck".

Check whether the selected wallet or model supports tool calls.

Open the artifact sidecar if the artifact card appears but the preview is hidden.

## Best Practices

Use Web Search for current information and Web Fetch for specific URLs.

Use files when you want the model grounded in uploaded documents.

Use Reference Conversations for prior chat context.

Use Memory for reusable cross-conversation context when enabled.

Use lower temperature for factual, technical, or structured tasks.

Use higher temperature for brainstorming and creative tasks.

Use Artifacts for charts, diagrams, documents, slides, and other outputs that need a preview or download.

Watch your wallet and token usage when using large models, long history, or multiple files.

Choose the smallest model that can reliably do the job.

Review generated content before using it in academic, professional, or public settings.

# Selecting Models

The model selector lets you choose which AI model responds in a conversation.

## Model Picker Layout

The model picker groups models by provider, tier, capability, and availability.

You may see provider branding for OpenAI, Anthropic, Google Gemini, Ollama, LiteLLM-routed models, or other configured providers.

Models may show metadata such as:

- Provider
- Model name
- Tier or capability group
- Cost information
- Context or output behavior
- Whether temperature, effort, image generation, audio, web tools, or other capabilities are supported

## Choosing a Model

Use stronger models for:

- Complex reasoning.
- Research synthesis.
- Coding and debugging.
- Long-form writing.
- Multi-step planning.
- Difficult document analysis.

Use smaller or faster models for:

- Quick questions.
- Draft rewrites.
- Classification.
- Simple summaries.
- Formatting.
- Low-risk brainstorming.

## Wallet-Aware Model Availability

DARE can route requests through different billing sources. The active wallet can affect which models and capabilities appear.

DARE Wallet: Uses platform-provided model access and deducts from your DARE balance.

Bring Your Own Key: Uses your own provider API keys when enabled for your account.

LiteLLM Wallet: Routes through a LiteLLM proxy key when available.

When using a LiteLLM wallet, some provider-native features may be unavailable because the proxy may not expose every capability. DARE hides unsupported toggles where possible.

## Model Switching

You can switch models during a conversation. The conversation history remains, but future responses use the newly selected model.

Use model switching to:

- Compare answers across providers.
- Use a faster model for simple follow-ups.
- Use a stronger model for difficult steps.
- Move from brainstorming to precise editing.

## Model Tiers and Cost Awareness

DARE groups models to help you understand tradeoffs between capability, cost, speed, and energy usage.

Before choosing a high-cost model, consider:

- Does the task require advanced reasoning?
- Does the model need a large context window?
- Are you asking for long output?
- Would a smaller model be sufficient?
- Is the conversation using many files or long history?

# Files

The Files section is where you upload, organize, share, and manage documents and media used across DARE.

## Files Interface Layout

### Top Controls

Upload Files: Add documents, images, audio, or other supported files.

Search: Find files by name or metadata.

Tags: Organize files by topic, course, project, or method.

Folders: Group files into collections.

Processing Files: View upload and processing progress.

### File List

The file list shows your uploaded files and metadata.

It may include:

- File name.
- File type.
- Upload date.
- Processing status.
- Tags.
- Folder association.
- Sharing status.
- Actions such as view, download, tag, move, share, or delete.

## Uploading Files

Open Files.

Click Upload or drag files into the upload area.

Wait for processing to complete.

Add tags or folders to keep the file organized.

Use the file later in conversations or workflows.

### File Processing

Uploaded files may go through processing before they are available for search or RAG.

Processing statuses include:

Processing: The file is being read, parsed, chunked, or indexed.

Completed: The file is ready to use.

Failed: The file could not be processed.

If processing fails, try uploading a cleaner copy, reducing file size, or using a supported format.

## File Tagging System

Tags help you organize files and select groups of files during conversations.

Use tags for:

- Course names.
- Research topics.
- Project codes.
- Reading lists.
- Data types.
- Source credibility.

When selecting documents for a conversation, tags can help you include multiple related files without selecting each one manually.

## Folders

Folders organize files into larger collections.

Use folders for:

- A single project.
- A class or module.
- A literature review.
- A client or partner folder.
- A workflow input set.

Folders are especially useful when a conversation or workflow needs several related documents.

## Sharing Files

If sharing is enabled for your account, you can share files with individual users or groups.

Shared files may appear in shared file views or be available to recipients based on permissions.

Use file sharing when:

- Collaborating with classmates or colleagues.
- Providing a dataset or reading packet to a group.
- Letting another user use a document in their own DARE work.

If your deployment uses access-code groups, group sharing may allow you to share with users in your assigned group.

## SyftBox-Backed Files

Some DARE deployments include SyftBox-backed storage or sync. When visible, SyftBox features help connect DARE file management with federated data storage and sharing.

You may see:

- SyftBox connection or authentication controls.
- Synced file locations.
- Public or shared file permissions.
- Background file sync status.

Use SyftBox-backed files when your project depends on shared or federated data workflows. If you do not see SyftBox controls, your account or deployment may not use this integration.

## Supported File Types

Supported file types depend on your deployment and enabled processors. Common document workflows include PDF, DOCX, TXT, Markdown, CSV, and similar text-based formats.

Media workflows may include images and audio when the corresponding conversation features are enabled.

For best results:

- Use clean text-based PDFs when possible.
- Avoid scanned PDFs unless OCR support is available.
- Keep filenames descriptive.
- Split extremely large documents into logical sections.
- Confirm processing completed before using a file in chat.

# Using Documents in Conversations

DARE supports document-grounded conversations. You can attach files directly, use RAG over processed documents, select tags or folders, and combine file context with prompts, agents, and conversation references.

## How RAG Works

RAG means Retrieval Augmented Generation. Instead of sending an entire document to the model every time, DARE searches processed document chunks and adds the most relevant excerpts to the model context.

RAG is useful because:

- It keeps prompts smaller.
- It helps the model focus on relevant passages.
- It works well for large collections.
- It can reduce cost compared with sending full documents.

RAG is not the same as full-file reading. It retrieves the most relevant chunks based on the query, so it may miss information if the question is broad, vague, or depends on exact page-level structure.

### Retrieval

When you ask a question with document context enabled, DARE analyzes your query and searches the selected documents for relevant sections. The system compares your question against processed document chunks and retrieves the best-matching snippets based on similarity.

This retrieval step is most useful when your documents are long, when you have selected several files, or when only a few parts of a document are relevant to the question.

### Augmentation

After retrieval, DARE combines several pieces of context:

- Your system prompt or selected prompt, if one is active.
- Your current message.
- Relevant conversation history based on the History Limit setting.
- Selected document snippets.
- Full-file context, if selected.
- Referenced conversations or memory, if enabled and selected.

The model receives this combined context and uses it to answer your question. This is why selecting only the documents that matter is important: unrelated files can make the model less focused and increase token usage.

### Generation

The AI then generates a response using both its general model knowledge and the context DARE provided from your materials. When document evidence is available, the response may include citations, source references, or matched snippets so you can see which parts of your documents influenced the answer.

This process allows the AI to work with information that may not have been in its original training data, including course readings, research papers, uploaded reports, project notes, or private reference material.

## Adding Documents to a Conversation

Open a conversation.

Click the file/document button near the message input.

Choose files, tags, folders, or other available context options.

Select whether to use embeddings, full files, or both when available.

Send your prompt.

### Document Selection Tabs

The document selector may include tabs or sections such as:

Files: Select individual uploaded files.

Tags: Select groups of files by tag.

Folders: Select files organized in a folder.

Media: Add images or audio where supported.

Context Settings: Adjust vector database or retrieval options when available.

### Vector Database Settings

The document selector may include retrieval controls. These settings determine how much document context DARE sends to the model.

Max Context Snippets: Controls the number of retrieved text segments included in the prompt. The default is designed for balanced performance. Increase this when you need broader coverage across a document set. Decrease it when you want sharper focus, lower cost, or faster responses.

Document Similarity Threshold: Controls how closely a document chunk must match your query before it is included. Lower values include broader, less similar matches. Higher values include only more closely related passages.

Use a lower threshold when you are exploring a topic, looking for loose connections, or unsure which terms appear in the source. Use a higher threshold when precision matters and you want to avoid tangential passages.

After selecting files and settings, click Done to confirm the active context. Check the status summary to confirm how many embeddings, files, tags, or folders are active.

## Understanding Context Options

### Embeddings vs Files

Embeddings: Use processed document chunks through RAG. Best for large files, collections, and targeted questions.

Files: Send fuller document context when supported. Best for exact review, formatting, comparison, or when every part of a document may matter.

### Tags and Folders

Tags and folders help you bring in multiple related files at once. They are useful when the model should answer from a project collection rather than one file.

### Vector Database Settings

Advanced context settings may allow you to adjust retrieval behavior, such as number of snippets or similarity threshold.

Use default settings unless you know you need a narrower or broader search.

## When to Use Each Approach

### Use Embeddings (RAG) When:

The document collection is large.

You need targeted answers from relevant excerpts.

You want to reduce context size and cost.

You are asking a specific question.

### Use Files (Full Context) When:

You need the model to review the entire document.

The document is short enough to fit comfortably.

Formatting, sequence, or exact wording matters.

The question depends on details spread across the whole file.

### Use Both Together When:

You want broad file awareness plus targeted retrieval.

You are doing complex analysis.

You are comparing multiple documents.

You are unsure whether retrieval alone will find the needed information.

## Practical Tips for Choosing

### Start with Embeddings if:

You are working with long PDFs.

You have many files.

Your question is specific.

You want faster or cheaper responses.

### Use Full Files if:

The file is short.

The model must inspect all content.

You are asking for a rewrite, critique, or detailed comparison.

Exact wording matters.

### Combine Both when:

The task is high value or complex.

You need both detail and breadth.

The model's first answer misses important parts of the source.

## Viewing Active Context

After adding files, check the active context indicators near the message input.

Remove files that are no longer needed.

Avoid attaching too many unrelated files, because extra context can increase cost and reduce answer quality.

If a response cites the wrong source or seems unfocused, narrow the selected files, tags, or folders.

## Matched Snippets and Source Review

When available, use Show Matched Snippets to inspect which parts of your documents were retrieved. This is one of the most useful debugging tools for document-grounded work.

If the matched snippets look relevant, the issue may be with the prompt or model. If the snippets look unrelated, adjust the file selection, tags, folders, similarity threshold, or wording of your question.

Matched snippets are especially useful for:

- Verifying that DARE found the correct section.
- Checking whether citations are grounded in the right document.
- Deciding whether to use full-file context instead.
- Debugging broad questions that retrieve scattered passages.
- Understanding why the model answered the way it did.

## Document-Grounded Prompting Tips

Ask specific questions when using embeddings. Instead of "What does this paper say?", ask "What statistical methods does this paper use to analyze temperature data?"

Name the document or section when possible. If several documents discuss similar topics, include the file name or topic in your prompt.

Ask for evidence. Phrases like "quote the relevant passage", "cite the source section", or "separate evidence from interpretation" help keep answers grounded.

Use full files for structure-sensitive tasks. If you need the model to understand argument flow, section order, formatting, or the relationship between distant parts of a document, full-file context may be better than retrieval alone.

Use both embeddings and files for complex analysis. For example, a contract review may need exact termination clauses from retrieval plus the overall risk framework from the full document.

# Prompts

Prompts are reusable templates that help you apply consistent instructions across conversations.

## Using System Prompts

Open a conversation.

Click the prompt button near the message input.

Choose a prompt from your saved prompts, shared prompts, or the prompt library.

Fill in variables if the prompt uses placeholders.

Send the prompt or combine it with your own message.

## Prompt Library Elements

### Prompt Selection Dialog

The prompt dialog includes tabs or sections such as:

My Prompts: Prompts you created.

Shared Prompts: Prompts shared with you, if sharing is enabled.

Library: Published or reusable prompts available to your account.

Agents: Agent templates available from the same selector.

Search: Filter prompts by title, description, or content.

Prompt Cards or Rows: Show the prompt name, version, creation date, description, and available actions.

Clone or Copy: Use this when you want to adapt an existing prompt without changing the original.

Apply: Adds the selected prompt to the active conversation.

Create Prompt: Opens the prompt creation flow.

## Creating New Prompts

Open Prompts from the sidebar.

Create a new prompt.

Give it a clear name and description.

Write the prompt content.

Use variables when you want reusable placeholders.

Save the prompt.

Test it in a conversation.

### Prompt Variables

Some prompts use variables or placeholders. Variables let you reuse the same prompt with different topics, documents, audiences, or output formats.

For example, a prompt might ask for:

- Topic.
- Audience.
- Reading level.
- Source material.
- Desired format.
- Evaluation criteria.

When you apply a prompt with variables, fill in each variable carefully. The quality of the output depends on both the prompt template and the values you provide.

## Prompt Versioning

When editing prompts, keep track of what changed and why.

Use prompt versions or cloning patterns when you want to compare approaches.

For important prompts, create a stable version before experimenting.

## Sharing Prompts

If sharing is enabled, you can share prompts with other users or groups.

Use sharing when:

- A prompt supports a class assignment.
- A team needs a common analysis template.
- You want collaborators to clone and adapt your prompt.

Recipients can use shared prompts without needing to recreate your instructions manually.

## Effective Prompt Design

Good prompts usually include:

- The role or perspective the AI should take.
- The task to complete.
- The context or source material to use.
- The desired output format.
- Constraints, such as length, tone, audience, or citation requirements.
- Examples when consistency matters.

Avoid prompts that combine too many unrelated goals. If a task has multiple stages, consider using a workflow.

### Prompt Design Examples

For analysis: "Act as a research analyst. Identify the main claim, supporting evidence, assumptions, and limitations. Use headings and cite the source sections where possible."

For writing: "Act as an editor. Rewrite the following text for a professional audience. Preserve the original meaning, improve clarity, and list the most important changes."

For extraction: "Extract the required fields into a table. If a field is missing, write 'Not found' instead of guessing."

For critique: "Evaluate this argument using the following criteria: accuracy, evidence quality, counterarguments, and clarity. Separate observations from recommendations."

### Prompt Maintenance

Review prompts that you use frequently.

Remove instructions that no longer match the platform or model behavior.

Keep successful examples in the prompt when format consistency matters.

Use versions to preserve a working prompt before making substantial changes.

Share only prompts that are general enough for others to use safely.

# Agents

Agents are reusable AI behavior templates. They package instructions and settings so you can apply a consistent role or method across conversations and workflows.

## What Are DARE Agents?

An agent can represent:

- A research assistant.
- A writing coach.
- A technical reviewer.
- A debate partner.
- A tutor.
- A data analyst.
- A workflow-specific role.

Agents help you avoid rewriting the same setup instructions every time.

Agents can combine:

- System prompts that define role and behavior.
- LLM models with different performance and cost characteristics.
- Content files that are always included.
- Embedding files used for RAG retrieval.
- RAG settings such as snippet count and similarity threshold.
- Generation parameters such as temperature and max tokens.
- Tool settings such as web search.

Key Benefits:

Reusability: Create once and use across multiple conversations or workflows.

Consistency: Apply the same model, prompt, files, and settings every time.

Efficiency: Avoid manually rebuilding complex configurations.

Organization: Manage AI roles in one central library.

Workflow Integration: Use agents as reusable building blocks in multi-step workflows.

## Accessing the Agents Section

Click Agents in the sidebar.

View your available agents.

Create, edit, clone, or delete agents depending on your permissions.

Agents may also appear in the prompt/agent selector inside conversations.

The Agents dashboard may show:

Agent: The agent name.

Prompt: The associated system prompt.

Temperature: The creativity or consistency setting.

Date Created: When the agent was created.

Action: Edit, clone, or delete controls.

Search Agents: Find agents by name, prompt, description, or role.

## Creating a New Agent

### Step 1: Open Agent Creation Dialog

Click the create or add agent control.

Start from a clear purpose.

### Step 2: Configure Basic Settings

Name: Give the agent a short descriptive name.

Description: Explain what the agent is for.

Instructions: Define the agent's behavior, role, boundaries, and output expectations.

Model: Choose the default model if the interface allows it.

Temperature: Set a default creativity level.

Web Search: Enable if the agent often needs current information.

Content Files: Attach specific documents that should always be included in the agent's context. Use content files for essential references, policies, rubrics, style guides, or instructions that the agent should always see in full.

Embedding Files: Attach documents that should be searched with RAG. Use embedding files for large knowledge bases, collections of readings, or reference sets where only relevant snippets should be retrieved.

Tags: If available, attach document tags so the agent can work over a maintained group of files without manually selecting each one.

Good Agent Names:

- Research Analyst.
- Data Extractor.
- Summary Writer.
- Policy Compliance Checker.
- Literature Review Assistant.
- Technical Documentation Reviewer.

Use names that describe the agent's job, not just the model it uses.

### Step 3: Configure Advanced Settings

Advanced agent settings may include:

- Max tokens.
- History or context behavior.
- File or RAG preferences.
- Web search behavior.
- Output formatting expectations.

Use advanced settings when the agent has a stable job that benefits from consistent behavior.

### Advanced Retrieval Settings

Max Context Snippets controls how many retrieved chunks the agent receives from embedding files. More snippets give broader context but increase token usage. Fewer snippets keep the agent focused but may miss relevant information.

Document Similarity Threshold controls how strict retrieval should be. Lower thresholds cast a wider net. Higher thresholds include only strongly related snippets.

Broad Discovery Pattern:

- Use more snippets.
- Use a lower threshold.
- Use when exploring unfamiliar material or connecting ideas.

Focused Review Pattern:

- Use fewer snippets.
- Use a higher threshold.
- Use when precision matters and tangential information would hurt quality.

### Advanced Generation Settings

Temperature controls the style of the agent's thinking and writing. Use lower temperature for extraction, compliance, grading, and technical review. Use higher temperature for brainstorming, ideation, and creative drafting.

Max Tokens controls how much the agent can produce. Short extraction agents can use lower limits. Report-writing or synthesis agents need more space.

Web Search should be enabled only when the agent's job requires current information. It can add latency and cost, so leave it off for stable document-grounded agents.

### Step 4: Save Your Agent

Save the agent.

Test it in a conversation.

Refine instructions based on actual output.

Clone the agent before making major experimental changes.

## Using Agents in Workflows

Agents are especially useful in workflow steps.

### Selecting an Agent in Workflow Steps

Open a workflow.

Add or select a step node.

Choose an agent for that step if the option is available.

Configure the step prompt and context.

Run or test the workflow.

### Benefits in Workflow Context

Consistency: Each step can use a stable role.

Specialization: Different steps can use different agents.

Reuse: Agents can be shared across multiple workflows.

Maintainability: Updating an agent can improve repeated tasks.

Example Multi-Agent Workflow:

Data Extractor Agent -> Analyst Agent -> Report Writer Agent.

The Data Extractor uses low temperature and structured output. The Analyst uses broader context and moderate temperature. The Report Writer uses a writing prompt and higher token limit. This is usually more reliable than asking one agent to do every step at once.

## Managing Your Agent Library

### Organization Best Practices

Use clear names.

Include the task domain in the description.

Separate narrow specialist agents from broad generalist agents.

Record model and temperature choices when they matter.

Keep old versions if you need reproducibility.

Group agents by function, domain, or complexity:

- Research, analysis, writing, extraction.
- Legal, medical, technical, creative, educational.
- Basic, intermediate, advanced.

Use the description field to explain when to use the agent, what files it depends on, and any limitations.

### Editing Existing Agents

Edit agents when instructions are unclear, outputs are inconsistent, or the task has changed.

After editing, test the agent on a known example.

Important: Changes to an agent can affect workflows that use that agent. For major changes, test in a cloned workflow or create a new agent version.

### Deleting Agents

Delete agents that are outdated, duplicated, or no longer useful.

Before deleting, confirm they are not used in important workflows.

### Searching and Filtering

Use search to find agents by name, description, or role.

Filter by ownership or use case when available.

## Agent Design Strategies

### Single-Purpose Specialists

Use when one task needs high consistency.

Examples:

- Literature review summarizer.
- Rubric-based assignment reviewer.
- Policy comparison assistant.
- Code documentation assistant.

### Multi-Purpose Generalists

Use for broad research or writing support.

Keep instructions flexible but clear.

Avoid overloading a generalist with too many conflicting rules.

### Domain-Specific Experts

Use for recurring domains such as education, finance, engineering, design, or policy.

Include domain constraints and expected terminology.

### Workflow Role-Based Agents

Use one agent per workflow role.

Examples:

- Extractor.
- Critic.
- Synthesizer.
- Fact checker.
- Formatter.

## Advanced Agent Configuration Patterns

### RAG Optimization Strategies

Use file context when the agent should answer from documents.

Keep retrieval settings focused for narrow tasks.

Use broader retrieval when the agent must synthesize across sources.

### Temperature Patterns by Task Type

| Task Type | Temperature Range | Rationale |
|---|---:|---|
| Data Extraction | 0.1 - 0.3 | Minimize variation and maximize consistency |
| Technical Documentation | 0.2 - 0.4 | Prioritize clarity and accuracy |
| Analysis & Interpretation | 0.4 - 0.6 | Balance insight with consistency |
| Content Generation | 0.5 - 0.7 | Produce readable and varied drafts |
| Brainstorming | 0.7 - 1.0 | Encourage diverse ideas |
| Creative Writing | 0.8 - 1.0 | Maximize creative variation |

### Token Allocation by Output Type

Short answer: 500-1,000 tokens.

Detailed explanation: 1,500-2,500 tokens.

Long-form draft: 3,000-6,000 tokens.

Artifact generation: 4,096+ tokens when supported.

## Agent Testing and Refinement

### Initial Testing

Test the agent with a simple example.

Check whether it follows instructions.

Check whether the tone and format match your expectations.

Check cost and output length.

### Iterative Refinement

Revise one instruction at a time.

Use examples when output format matters.

Remove contradictory instructions.

Keep the agent's purpose narrow enough to evaluate.

### A/B Testing Agents

Clone an agent.

Change one factor, such as model, temperature, or instructions.

Run both agents on the same task.

Compare accuracy, usefulness, cost, and consistency.

## Cost Management with Agents

### Cost Factors

Model choice.

Max token settings.

History length.

File context.

Web search or web fetch.

Tool and artifact use.

Number of workflow steps using the agent.

### Cost Optimization Strategies

Use smaller models for simple steps.

Lower max tokens when outputs should be short.

Use targeted RAG instead of sending too many full files.

Avoid high temperature when consistency is more important than variation.

Reuse successful agents rather than repeatedly experimenting from scratch.

## Common Agent Use Cases

### Research Workflows

Literature review.

Source comparison.

Argument mapping.

Hypothesis generation.

### Content Creation Workflows

Drafting.

Editing.

Tone adaptation.

Slide or report generation.

### Data Processing Workflows

Extraction.

Classification.

Summarization.

Structured output generation.

### Analysis Workflows

Policy analysis.

Technical review.

Rubric evaluation.

Scenario planning.

## Troubleshooting Common Issues

### Problem: Agent outputs are inconsistent

Lower temperature.

Clarify instructions.

Use examples.

Reduce unnecessary context.

### Problem: Agent missing relevant information

Add or narrow document context.

Enable web search or web fetch if current or external sources are needed.

Increase history or reference relevant conversations.

### Problem: Agent outputs are too similar to source material

Ask for synthesis rather than paraphrase.

Require citations or source separation.

Add instructions about original analysis.

### Problem: Agent responses are too short

Increase max tokens.

Ask for detailed reasoning or examples.

Specify the expected structure.

### Problem: Agent responses are too long or verbose

Lower max tokens.

Ask for concise output.

Add section or word limits.

### Problem: High costs from agent usage

Use a lower-cost model.

Reduce max tokens.

Reduce history and file context.

Avoid unnecessary tool use.

### Problem: Agent not using web search when needed

Confirm web search is enabled for the agent or conversation.

Ask explicitly for current sources.

Use Web Fetch for known URLs.

## Best Practices Summary

Design agents around clear roles.

Keep instructions specific and testable.

Use examples for formatting.

Match model and temperature to task difficulty.

Clone before major experiments.

Monitor cost when agents are used in workflows.

Review outputs before relying on them.

# Workflows

Workflows let you build multi-step AI processes on a visual canvas. They are useful when a task is too complex for one prompt or when you want repeatable structure.

## What Are DARE Workflows?

A workflow is a graph of connected nodes.

Each node performs a role such as receiving input, running an AI step, branching conditionally, producing structured output, or showing final results.

Workflows are useful for:

- Research pipelines.
- Document review.
- Multi-stage analysis.
- Content generation.
- Data extraction.
- Batch processing.
- Teaching and learning activities.

## Understanding Workflow Components

### The Visual Canvas

The canvas is where you build the workflow.

You can add nodes, connect nodes, move them around, rename them, and configure each node.

Workflow builder pages open in a full-canvas view so you have more room to design and test.

### Four Types of Workflow Nodes

The original workflow model centered on four core node concepts: Start, Step, Conditional, and Structured Output. The current builder keeps those ideas but presents the interface with updated components such as Start, Step, Conditional, File, and Note nodes, plus output behavior inside the execution flow.

Think of these as the main building blocks:

- Start nodes introduce the workflow input.
- Step nodes perform LLM work.
- Conditional or structured output nodes decide where the workflow should go next.
- Output-oriented nodes or final steps return results to the user.

The newer File and Note components make workflows easier to maintain by separating file retrieval and canvas documentation from model reasoning.

### Workflow Nodes

Available node types may include:

Start Node: Entry point and initial context.

Step Node: Runs an LLM prompt, optionally using files, tags, agents, or previous outputs.

Conditional Node: Routes execution based on a condition.

Structured Output Node: Produces structured fields or routes.

Chat Output Node: Sends output back to the user.

File Node: Provides file-based input when available.

Notes Node: Adds human-readable notes or planning context on the canvas.

The exact node set can vary as the workflow builder evolves.

#### 1. Start Node

The Start Node defines where a workflow begins.

Use it to:

- Define the initial input.
- Choose sequential or batch-style execution where available.
- Attach the first set of documents or instructions.
- Establish the purpose of the workflow.

Best for:

- Single-document workflows.
- Batch workflows that process multiple files.
- Parallel analytical frameworks that begin from separate start points.

Configuration Tips:

- Use a clear starting instruction.
- Attach only files that every downstream step needs.
- Keep the start context general and let later steps specialize.
- If a workflow has multiple independent analysis paths, make sure each path has a complete downstream route.

#### 2. Step Node

Step Nodes perform the main LLM processing in a workflow.

Use them to:

- Run a prompt.
- Apply an agent template.
- Select a model.
- Attach files, embeddings, or tags.
- Enable web search when needed.
- Use previous step output as context.

Step Nodes are best for work that can be described as a single operation, such as extracting facts, summarizing a source, evaluating quality, generating a draft, or synthesizing prior outputs.

Good Step Node design:

- Give the step a descriptive label.
- Make the prompt specific.
- Use an agent when the same configuration will be reused.
- Enable previous context only when the step actually needs earlier output.
- Set token limits based on the expected output size.
- Use lower temperature for extraction and higher temperature for ideation.

#### 3. Conditional Node

Conditional logic is used when a workflow needs to choose between routes.

For example, a workflow might evaluate whether a document is complete, whether a draft meets a rubric, whether a source is relevant, or whether a case should receive simple or detailed analysis.

Use conditional routing when the next step should depend on the content of the previous result.

#### 4. Structured Output Node

The current builder may label this component as Conditional while using structured output behavior underneath. It routes execution based on a decision, field, or category.

Use it to:

- Route high-quality output to finalization and low-quality output to revision.
- Send simple cases through a shorter path and complex cases through deeper analysis.
- Separate different document types.
- Apply human validation at important decision points.
- Produce structured fields that later steps can depend on.

Good conditional design:

- Define objective criteria.
- Make route labels clear.
- Include examples for borderline cases.
- Use lower temperature when consistent routing matters.
- Add human validation when the decision has consequences.

### Chat Output Node

Chat Output Nodes return final or intermediate results to the user.

Use them when:

- A workflow result should be visible in the chat-style output.
- You want a clear final response.
- A branch should end with a specific message or summary.

Output nodes should explain what the workflow produced and, when useful, what next action the user should take.

### File Node

File Nodes provide file retrieval or file-based input inside a workflow.

Use them when:

- A workflow step needs a particular file set.
- You want to separate file selection from LLM processing.
- You are building a reusable workflow where inputs may change.

Keep file nodes organized and clearly labeled. If a file node feeds several downstream steps, make sure those steps all genuinely need that file context.

### Notes Node

Notes Nodes are for documentation on the canvas.

Use them to:

- Explain workflow purpose.
- Mark sections of the canvas.
- Record assumptions.
- Leave instructions for collaborators.
- Document why a route exists.

Notes do not replace clear node labels, but they make complex workflows easier to maintain.

### Edges and Data Flow

Edges connect nodes and determine execution order.

A step can use previous node output when context passing is enabled.

Cycle detection helps prevent invalid connections.

Use clear labels so the flow is understandable when you return later.

### Canvas Controls

The workflow builder supports direct manipulation of nodes and edges. You can place nodes on the canvas, connect them visually, rename labels, and configure selected nodes from the side panel.

Use Clear All only when you intend to remove the current canvas structure. For experimentation, cloning the workflow is safer than clearing a working design.

Cycle detection helps prevent loops that would make execution ambiguous or unsafe. If a connection is rejected, review whether the output direction is correct and whether the workflow can be represented as a clear process.

## Building Your First Workflow

### Step 1: Access Workflows

Click Workflows in the sidebar.

Create a new workflow or open an existing one.

### Step 2: Configure Start Node

Define the starting input.

Describe what the workflow is meant to accomplish.

Add any initial instructions or files.

### Step 3: Add Step Nodes

Add one step for each distinct task.

Give each step a clear label.

Choose the model, prompt, agent, and context for the step.

Keep each step focused.

### Step 4: Add Conditional or Routing Logic

Use conditional nodes when different outputs require different paths.

Keep conditions simple and test them carefully.

Use structured outputs when later steps need reliable fields.

### Step 5: Complete the Workflow

Add final output nodes.

Check that every path reaches a useful output.

Validate the canvas before running.

### Step 6: Test Your Workflow

Run the workflow on a small example.

Inspect each step output.

Adjust prompts, models, and context.

Clone the workflow before major changes.

### Step 7: Review and Refine

After a test run, inspect each step's output rather than only reading the final result.

Ask:

- Did the first step extract the right information?
- Did later steps receive the right context?
- Did conditional routes make the expected decision?
- Did any step use too many tokens?
- Did the selected model match the task difficulty?

Make one change at a time. If you adjust the prompt, model, and files all at once, it becomes harder to know which change improved or harmed the workflow.

## Workflow Execution Controls

### Manual Mode vs. Automated Execution

Manual Mode: Allows you to run or approve steps one at a time.

Automated Execution: Runs the workflow through the graph without stopping at every step.

Use Manual Mode when:

- A human should review intermediate outputs.
- The workflow is new or untested.
- Errors could be costly.
- You are teaching or demonstrating each step.

Use Automated Execution when:

- The workflow is stable.
- The task is repetitive.
- You trust the prompts and routing.
- You are processing multiple inputs.

Manual Mode is especially useful during workflow development. It lets you pause after each step, inspect output quality, and adjust prompts before continuing. Use it when you are debugging, teaching, refining, or working with sensitive material.

Automated Execution is better for production-style use. Once a workflow has been tested, automated execution can process inputs faster and more consistently.

Hybrid Approach:

- Develop and test in Manual Mode.
- Clone or version the workflow before major changes.
- Switch to Automated Execution after validation.
- Continue using output versions to compare changes over time.

### Output Version Control

Workflow runs can preserve outputs from previous executions.

Use version history to:

- Compare prompt changes.
- Review improvements.
- Recover a useful earlier output.
- Document how a result was produced.

Each step's output may be versioned independently. This lets you compare how a prompt, model, file set, or routing change affected only one part of the workflow.

Version control is useful for:

- Comparing prompt revisions.
- Testing different document configurations.
- Reviewing route decisions.
- Maintaining an audit trail of workflow development.
- Recovering a better previous output after an experimental change.

## Running and Monitoring Workflows

### Executing a Workflow

Open the workflow.

Review node configuration.

Click Run or the available execution control.

Monitor progress in the execution panel.

Review each output.

For automated execution, confirm Manual Mode is off before running. For manual execution, confirm Manual Mode is on and step through the workflow deliberately.

If the workflow uses batch or parallel processing, start with a small input set. Once the workflow behaves as expected, scale up to larger batches.

### Monitoring Workflow Execution

Workflow execution may stream updates in real time.

You may see:

- Running nodes.
- Completed nodes.
- Failed nodes.
- Intermediate step responses.
- Batch progress.
- Final output.

The execution interface may also show:

Status: Overall state such as running, paused, completed, or failed.

Duration: How long the workflow has been running.

Timeline: Start and completion timestamps.

Step Status: Not started, running, completed, awaiting decision, or failed.

Expandable Previews: Step outputs you can open and inspect.

Human Validation Interface: Route recommendation, reasoning, and continue/cancel controls when validation is triggered.

### Workflow Outputs

Outputs may include:

- Text responses.
- Structured data.
- Files or file references.
- Artifact references.
- Chat output nodes.
- Exportable PDF summaries where available.

After completion, review the full execution path. Check which route was taken at each conditional node and whether the reasoning matches your expectation.

Export results when you need a record outside DARE. Exported workflow results are useful for research notes, class review, documentation, or reporting, but review them before sharing.

## Managing Your Workflows

### Cloning Workflows

Clone a workflow when you want to experiment without changing the original.

Use cloned workflows for:

- New model choices.
- Prompt changes.
- Different file sets.
- New routing logic.

Clone workflows when you want to:

- Create a template for a team.
- Adapt a successful workflow to a related project.
- Test a new routing pattern.
- Preserve a known-good version before experimenting.
- Build a library of common processes.

### Sharing Workflows

If sharing is enabled, share workflows with collaborators or groups.

Use sharing for class templates, research team processes, or repeated organizational tasks.

### Exporting Workflows

The workflow editor may allow export of workflow definitions for reuse or transfer.

Exported workflows should be reviewed before sharing outside your project, especially if prompts contain sensitive context.

### Workflow Organization Best Practices

Use descriptive names.

Add notes on the canvas.

Label nodes clearly.

Keep related steps visually grouped.

Use tags when available.

Remove obsolete nodes.

Complexity Guidelines:

Simple workflows usually have 2-3 processing steps.

Moderate workflows usually have 4-6 steps and one or two decisions.

Complex workflows may have 7 or more steps, multiple routes, batch processing, or human validation.

Document complexity in the workflow description so other users know what to expect.

### Workflow Documentation

Document:

- Workflow purpose.
- Required inputs.
- Expected outputs.
- Model choices.
- Known limitations.
- When to use manual mode.

Also document:

- Decision points and routing criteria.
- Required human validation points.
- Recommended file types.
- Expected runtime or cost profile.
- Known failure cases.

## Common Workflow Patterns

### Pattern 1: Linear Sequential Processing

Start -> Extract -> Analyze -> Summarize -> Output

Use for straightforward multi-step work.

Best for: Single documents requiring progressive refinement.

Example: Research paper analysis where one step extracts methods, a second step evaluates findings, and a third step writes a summary.

### Pattern 2: Quality Gate with Conditional

Start -> Draft -> Evaluate -> If acceptable, output; if not, revise.

Use when outputs must meet criteria.

Best for: Quality control, compliance review, rubric checks, and sensitive content.

Add human validation when the accept/revise decision should not be left entirely to the model.

### Pattern 3: Parallel Processing with Convergence

Start -> Multiple analysis paths -> Synthesis -> Output

Use when you want different perspectives or methods.

Best for: Comparative research, multi-framework analysis, and synthesis from multiple sources.

Use a final synthesis step to combine outputs into one coherent result.

### Pattern 4: Complexity-Based Routing

Start -> Assess complexity -> Simple path or detailed path

Use when inputs vary in difficulty.

Best for: Efficient resource allocation. Simple inputs avoid unnecessary expensive steps, while complex inputs receive deeper analysis.

### Pattern 5: Multi-Stage Validation

Start -> Extract -> Validate -> Correct -> Finalize

Use for data extraction and review.

Best for: Legal review, publication approval, financial review, assessment workflows, or any process that needs multiple gates.

### Pattern 6: Parallel Multi-Framework Analysis (Multiple Start Nodes)

Start -> Framework A, Framework B, Framework C -> Compare -> Output

Use when a problem should be examined through multiple lenses.

Best for:

- Academic research with multiple theoretical perspectives.
- Business analysis using different frameworks.
- Content evaluation using different quality criteria.
- Policy analysis through legal, economic, and social lenses.

Example: Analyze a policy document through legal, economic, and social impact frameworks, then synthesize the findings into one assessment.

## Best Practices for Workflow Design

### Design Principles

Make each node do one clear job.

Use descriptive labels.

Pass only the context each step needs.

Test conditions with realistic inputs.

Prefer manual mode until the workflow is stable.

Ensure each route leads to a meaningfully different process. If both routes do almost the same thing, simplify the workflow.

Use strategic validation. Human validation is valuable, but too many pauses make workflows slower and harder to run.

Keep outputs compatible. If one step produces prose and the next step expects a table, tell the first step to produce the table or tell the second step how to transform the prose.

### When to Use Manual Mode

Use Manual Mode when:

- You need human judgment.
- Outputs will be used externally.
- The workflow is experimental.
- The task involves sensitive data.
- A failed step would waste cost or time.

Use Automated Execution when:

- The workflow is already tested.
- The process is routine.
- You are running a batch.
- You need quick repeatable processing.
- You do not need to inspect every intermediate result.

### Prompt Engineering for Workflows

Use explicit output formats.

Tell each step what previous output means.

Avoid vague conditions.

Use structured output when later nodes depend on specific fields.

Keep prompts short enough to debug.

Sequential Workflow Prompts:

- Reference previous steps explicitly.
- Tell the model where it is in the workflow.
- Ask for output that the next step can use.
- Include fallback instructions if prior output is insufficient.

Conditional Evaluation Prompts:

- Define routing criteria clearly.
- Explain what each path means.
- Include boundary cases.
- Ask for reasoning when human validation is enabled.

Structured Output Prompts:

- Use stable field names.
- Avoid ambiguous categories.
- Keep route options limited.
- Make downstream expectations explicit.

### Context Management

Pass previous context only when necessary.

Use tags and files intentionally.

Avoid sending unrelated documents to every step.

Use agents for repeated roles.

Initial research steps often need source documents. Later analysis steps may only need the previous step output. Synthesis steps may need both prior outputs and original sources. Review or validation steps often need the content being reviewed plus the criteria.

Use descriptive file names, tags, and folders so workflow configuration is easier to understand later.

### Performance Optimization

Use smaller models for simple steps.

Use stronger models only where needed.

Limit token output for intermediate nodes.

Batch only after testing on a single input.

Place conditional nodes strategically to avoid unnecessary processing. For example, triage first, then send only complex cases to expensive models.

Monitor token usage across the whole workflow. A cheap individual step can become expensive when repeated across many files or branches.

## Troubleshooting Common Issues

### Problem: Workflow fails at a specific step

Open the failed node.

Review its prompt, files, model, and previous input.

Run the step manually if possible.

Simplify the prompt and retry.

Check that all required files are uploaded and accessible.

Review token limits; outputs may be truncated.

Test the problematic step in a regular conversation with the same model and files.

### Problem: Conditional routing is inconsistent

Make the condition more explicit.

Use structured outputs.

Lower temperature.

Add examples of expected routing.

Enable human validation while testing.

Review whether the evaluation prompt asks for a clear decision or only general analysis.

### Problem: Human validation pauses are not triggering

Confirm Manual Mode is enabled.

Check the node configuration.

Review whether the workflow path reaches that node.

Verify that human validation is enabled on the conditional or structured output node.

Check that the node is properly connected and produces a routing decision.

Review execution logs or status messages for errors.

### Problem: Human validation pauses aren't triggering

If you are looking for the older troubleshooting label, this is the same issue as "Human validation pauses are not triggering." The most common causes are that validation is not enabled on the decision node, the workflow never reaches that route, or the conditional prompt does not produce a clear decision.

### Problem: Parallel execution not working as expected

Check node connections.

Confirm there are no invalid cycles.

Review whether all required upstream nodes completed.

Confirm the relevant start or batch mode is configured correctly.

Check that each input document is accessible and formatted properly.

Review whether each branch can run independently.

### Problem: Outputs are not flowing between steps properly

Confirm context passing is enabled where needed.

Check the edge direction.

Review each step output.

Avoid relying on hidden assumptions between nodes.

Check that the output format from the previous step matches what the next step expects.

Update prompts so each step explicitly references the previous output when needed.

Run the workflow step-by-step to identify where the chain breaks.

### Problem: Outputs aren't flowing between steps properly

This usually means either the nodes are not connected in the intended direction, previous context is not enabled where needed, or the upstream output format does not match what the next step expects. Fix the flow before changing models; routing and context issues often look like model quality problems.

### Problem: Manual Mode not advancing to next step

Confirm the current step completed.

Check for validation or approval controls.

Review execution status.

Refresh only if needed, because running state may still be updating.

Check whether the next step is missing a required input, file, prompt, model, or route.

Try resetting the run and executing again from the beginning.

If automated execution succeeds but manual mode does not, inspect the manual approval or step controls.

### Problem: Version control dropdown not showing previous runs

Run the workflow at least once.

Confirm outputs were saved.

Check whether you are viewing the correct workflow.

Refresh the page if a recent run does not appear immediately.

Confirm you are looking at the correct step's output versions.

If the workflow was heavily modified, older versions may not map cleanly to the current node structure.

### Problem: Multiple Start nodes not executing in parallel

Verify each Start node is properly configured.

Check that Start nodes are not accidentally connected to each other.

Ensure each Start node has a complete downstream path.

Review execution logs to confirm which chains started.

Test each Start node chain independently before running them together.

### Problem: Batch execution is hard to monitor

Use a small batch first.

Watch the batch progress panel.

Review failed items separately.

## Advanced Workflow Techniques

### Multi-Path Analysis with Convergence

Run several analysis paths in parallel and synthesize the results.

Use when you want different frameworks or agents to analyze the same input.

### Adaptive Depth Processing

Route simple cases through a short path and complex cases through a deeper path.

Use when inputs vary in difficulty.

### Iterative Refinement with Validation

Generate an output, evaluate it, and revise only if needed.

Use when quality matters more than speed.

### Batch Processing with Exception Handling

Run a stable workflow over multiple files or inputs.

Review exceptions separately.

Use batch mode only after testing the workflow on a representative single input.

## Use Cases for Workflows

### Research and Academic

Literature review.

Paper comparison.

Rubric-based assessment.

Hypothesis development.

### Business and Professional

Proposal review.

Meeting synthesis.

Policy analysis.

Competitive research.

### Content Creation

Blog drafts.

Slide outlines.

Editing pipelines.

Multi-format content adaptation.

### Education and Training

Lesson planning.

Student feedback.

Practice question generation.

Reflection analysis.

## Future Enhancements

DARE workflows continue to evolve. Treat this section as availability guidance rather than a guarantee that every item appears in every deployment.

Areas that may continue to improve include:

RAG-Based Workflow Agents: More direct use of retrieval inside workflow steps, so agents can combine system prompts, document retrieval, and step-specific context more naturally.

Enhanced Conditional Interfaces: Larger editing areas, clearer route builders, and reusable conditional templates for common evaluation patterns.

Workflow Analytics: Better visibility into success rates, routing patterns, execution time, and cost across repeated workflow runs.

Artifact-Aware Workflows: Deeper support for generating, revising, and exporting artifacts as part of repeatable workflow processes.

Collaboration Improvements: More workflow sharing, group templates, and review patterns for teams, courses, and research groups.

## Conclusion

Workflows help you turn repeated prompting patterns into repeatable processes. Start simple, test carefully, and expand only after the workflow reliably handles real inputs.

# Integrations

Integrations connect DARE to external tools and data sources through Model Context Protocol (MCP). This section appears as Integrations when MCP is enabled for your account.

## What Are MCP Integrations?

MCP servers expose tools that an AI model can use during a conversation.

Examples include:

- Research tools.
- File or data tools.
- Hosted external services.
- SyftBox-related tools.
- Team-specific systems configured by administrators.

Integrations allow DARE to move beyond chat responses and use approved tools directly.

## Accessing Integrations

Click Integrations in the sidebar.

The Integrations page shows available MCP servers.

Each server card may show:

- Name.
- Description.
- Icon.
- Connection status.
- Available tools.
- Setup or credential requirements.

## Connecting to an MCP Server

Open the server detail page.

Review setup instructions.

Connect using the required method:

- No authentication.
- API key or bearer token.
- OAuth redirect.
- One-time password or service-specific flow.

After connecting, the server's tools become available where supported.

## Remote MCP Servers

If your account role allows it, you may see Add Remote MCP.

Use this to add a hosted Streamable HTTP MCP server to the curated catalog.

Required details may include:

- Name.
- Slug.
- Remote MCP URL.
- Authentication type.
- OAuth details if required.
- Credential help URL.
- Setup guide.

Only add trusted MCP servers. Tools can access external services, so follow your organization's security and data policies.

## Using MCP Tools in Conversations

In a conversation, select connected MCP servers using the MCP server selector.

When a model decides a tool is useful, DARE can call that tool and return the result to the model.

Tool use may appear as:

- Tool call indicators.
- Tool result summaries.
- Source or execution cards.
- Follow-up AI responses that use the tool result.

Use MCP tools when:

- The AI needs live or external data.
- A task requires an action outside normal text generation.
- You need access to a specialized tool provided by your organization.

## Tool Execution Page

Some MCP tools can be run from the Integrations page.

Open a server.

Choose a tool.

Fill in the generated form.

Execute the tool.

Review the JSON or formatted result.

This is useful for testing a tool before using it in a conversation.

## Execution History

The Integrations area may include an execution history page.

Use execution history to review:

- Which tools were called.
- When tools were executed.
- Whether execution succeeded or failed.
- Inputs and outputs where available.

## Troubleshooting Integrations

If a server is not connected, check credentials or OAuth status.

If a tool fails, review required inputs.

If a tool is missing in conversation, confirm the server is selected.

If an external service rejects the request, reconnect or update credentials.

If you do not see Integrations, the feature may not be enabled for your account.

# Memory

Memory allows DARE to remember useful information across conversations. This section appears when Memory is enabled for your account.

## What DARE Remembers

Memory can store recurring information such as:

- Your preferences.
- Project context.
- Research interests.
- Writing or formatting preferences.
- Repeated facts you have asked DARE to keep in mind.

Memory is meant to improve continuity. It is not a substitute for attaching important source files or providing precise instructions in high-stakes work.

## Accessing Memory

Click Memory in the sidebar.

The Memory page shows what DARE remembers about you across conversations.

You may see:

- Total memory count.
- Categories.
- Last updated time.
- Memory type filters.
- All Memories tab.
- Search tab.
- Seed Memory button.
- Clear All button.
- Delete controls for individual memories.

## Searching Memory

Open the Search tab.

Enter a query.

Review matching memories and categories.

Delete irrelevant memories if needed.

## Seeding Memory

The Seed Memory action helps DARE create memory items from existing context when supported.

Use it when:

- You want DARE to initialize memories from prior work.
- You want better continuity across future conversations.
- You have important recurring context in your account.

Review seeded memories afterward and remove anything inaccurate.

## Using Memory in Conversations

Open the Reference Conversations panel.

Turn on Memory.

Send your prompt.

When DARE uses memory, memory sources may appear under the AI response.

Use Memory for helpful continuity. Turn it off when you want an isolated answer.

## Managing Memory

Delete individual memories that are outdated or incorrect.

Use Clear All if you want to reset memory completely.

Review memory periodically, especially after major project changes.

Do not rely on memory for confidential or exact source material. Use files and explicit prompts when accuracy matters.

# Billing and Cost Tracking

The Cost Tracking section shows your wallet balance, active billing options, and transaction history.

## Wallet Balance

### Current Balance Display

Your DARE wallet balance shows available platform credit.

If multiple wallet types are available, the wallet section shows each billing source you can use.

Common wallet types include:

DARE Wallet: Platform-provided credit managed by DARE.

Bring Your Own Key: Uses your configured provider API keys when enabled.

LiteLLM Wallet: Uses a LiteLLM proxy key, which may be self-served, admin-issued, or cohort-based.

## Active Wallet Selection

Use the wallet picker or wallet list to choose which billing source is active.

The active wallet determines how future model requests are paid for.

DARE validates whether the selected wallet is still available. If a wallet expires, is disabled, or is no longer valid for your account, DARE may fall back to the DARE wallet or ask you to choose another valid option.

## Bring Your Own Key

If Bring Your Own Key is enabled for your account, you can use your own provider keys instead of DARE wallet credit for supported providers.

Use BYO keys when:

- You have your own provider billing.
- You need model access tied to your project or team.
- You want costs to appear in your provider account.

Keep API keys secure and remove unused keys.

## LiteLLM Wallets

LiteLLM wallets let DARE route through a LiteLLM proxy key.

You may be able to:

- Add a LiteLLM key.
- Rename a key.
- Delete a key.
- Test a saved or unsaved key.
- Select a LiteLLM key as your active wallet.

The model picker can show models available through the selected LiteLLM wallet. DARE may cache the probed model list briefly for performance.

Some provider-native features may not appear while using LiteLLM, because the proxy may not support or expose them in the same way as direct provider access.

## Transaction History

### Transaction Table

The transaction table shows recent usage and charges.

It may include:

- Date.
- Model or provider.
- Platform.
- Billing mode.
- Input tokens.
- Output tokens.
- Cost.
- Status.

### Transaction Filters

Use tabs to filter transactions by billing mode, such as all transactions, wallet usage, own API key usage, or LiteLLM usage.

Use the platform filter to focus on DARE, partner platforms, or all platforms when available.

### Export Transaction History

Click Export Transaction History to download a CSV of the current transaction history.

Use exported CSVs for:

- Personal budgeting.
- Research accounting.
- Group review.
- Course or project reporting.

## Understanding Costs

Costs depend on:

- Model selected.
- Provider pricing.
- Input tokens.
- Output tokens.
- File context.
- Conversation history.
- Web search or web fetch.
- Tool use.
- Artifact generation.
- Audio transcription or image generation.

Long conversations, large files, and high max-token settings increase usage.

## Managing Your Budget

### Monitoring Usage

Check the Dashboard for summary metrics.

Use Cost Tracking for detailed transaction history.

Review which wallet is active before running expensive tasks.

Watch for high token usage when using files, long history, or artifact generation.

### Cost Optimization Strategies

Use smaller models for simple tasks.

Lower Max Tokens for short answers.

Lower History Limit when earlier messages are not needed.

Use targeted files instead of large unrelated document sets.

Disable optional tools when not needed.

Use Web Fetch only for sources you actually want analyzed.

Use workflows carefully, because each step can create model usage.

## Administrative Information

### Wallet Refills and Policies

Wallet refill behavior depends on your deployment and group policy. Some accounts may receive scheduled refills, group allocations, or administrator-managed budgets.

If your balance seems incorrect, check Cost Tracking first, then contact support or your group administrator.

### Billing Reports

Administrators may have additional usage dashboards and breakdowns. End users should use Cost Tracking and exported CSVs for normal review.

# Group Wallets

Group Wallet appears when you own or manage a wallet-enabled group.

## What Group Wallets Are

Group Wallets let a group owner manage budget and allocation for users in a group.

They are useful for:

- Courses.
- Research groups.
- Project teams.
- Cohorts using shared AI resources.

## Group Wallet Manager

The Group Wallet page may show:

- Owned groups.
- Group budget.
- Policy settings.
- Member balances.
- Allocation controls.
- User override controls.

## Allocating Budget

Use allocation controls to distribute budget to members.

Check member balances before large assignments or group activities.

Use overrides when a specific user needs a different policy than the group default.

## Group Wallet Best Practices

Set expectations before users begin high-cost tasks.

Review member usage regularly.

Use smaller models for routine coursework.

Reserve high-cost models for tasks that require them.

Explain when students or team members should use BYO keys or LiteLLM wallets if those options are available.

# Learning Progression

## Beginning: Basic Conversations

Start with simple conversations.

Learn how to choose models.

Adjust temperature and max tokens.

Try Web Search only when current information matters.

Watch token usage and cost.

## Intermediate: Document Integration

Upload files.

Add tags and folders.

Use RAG for targeted document questions.

Use full files when exact document review matters.

Reference earlier conversations.

Experiment with prompts and agents.

## Advanced: Tool Use and Artifacts

Enable Artifacts for structured outputs.

Create charts, diagrams, documents, and slide drafts.

Connect Integrations when MCP tools are useful.

Review tool call results and artifact versions.

Export or download outputs as needed.

## Advanced: Workflow Creation

Build workflows for repeatable multi-step tasks.

Use agents for specialized roles.

Use Manual Mode until the workflow is reliable.

Add conditional logic and structured output when needed.

Monitor cost for multi-step and batch workflows.

## Advanced: Memory and Collaboration

Use Memory for recurring personal or project context.

Share prompts, conversations, workflows, or files when enabled.

Use group wallets and budget controls when managing a team or course.

# Troubleshooting & Support

## Common Issues

### Document Integration Issues

Confirm the file processed successfully.

Use supported file types.

Try a cleaner copy of the document.

Narrow selected files, tags, or folders.

Use full-file context when exact wording matters.

Use RAG for targeted questions across large files.

### Response Quality Issues

Clarify the prompt.

Choose a better-suited model.

Adjust temperature.

Increase or decrease history limit.

Remove irrelevant context.

Use examples for desired output format.

### Model Performance

If a model is slow, try a smaller model.

If a model lacks a needed feature, choose one that supports it.

If a model does not show temperature or effort controls, that capability may not be supported.

If a LiteLLM-routed model is missing features, switch wallets or providers if available.

### Billing Problems

Check the active wallet.

Review transaction history.

Confirm your DARE balance is sufficient.

Test BYO or LiteLLM keys if using them.

Contact your administrator for group wallet or refill questions.

### Sharing Problems

Confirm sharing is enabled for your account.

Check whether the recipient is in your group or has platform access.

Verify permissions.

For files, confirm the file is still available and processed.

### Integration Problems

Confirm Integrations is enabled.

Reconnect the MCP server.

Check OAuth or API key status.

Run the tool directly from the Integrations page if available.

Review execution history for errors.

### Memory Problems

Confirm Memory is enabled.

Turn on Memory in the Reference Conversations panel.

Review memory sources under responses.

Delete inaccurate memories.

Clear all memory if you need a full reset.

### Artifact Problems

Confirm Artifacts is enabled.

Ask for a specific artifact type.

Open the artifact sidecar.

Use version dropdowns to inspect updates.

Try downloading in a different format if one export fails.

## Getting Support

Use the Help page for platform guidance.

Contact your course, group, or platform administrator for account-specific access.

When reporting a problem, include:

- What you were trying to do.
- Conversation or workflow name.
- Model selected.
- Active wallet type.
- Files or tools used.
- Error message or screenshot.
- Approximate time of the issue.

# Conclusion

DARE is designed to support transparent, flexible, and accountable work with large language models. You can begin with simple conversations, then gradually add files, prompts, agents, workflows, integrations, memory, artifacts, and cost controls as your work becomes more complex.

The most effective DARE use comes from matching the tool to the task: choose the right model, provide the right context, use optional capabilities only when they help, and review outputs carefully. As the platform evolves, features may appear based on your account, group, or deployment, but the core workflow remains the same: define the task clearly, choose appropriate context and settings, inspect the result, and iterate.
