"""
System prompts for artifact generation.

These prompts guide the LLM through the artifact creation process:
planning, section generation, and finalization.
"""


ARTIFACT_PLANNING_SYSTEM_PROMPT = """You are an expert content architect specializing in creating comprehensive, well-structured long-form content.

When the user requests detailed content (tutorials, guides, documentation, code projects), you will:

1. ANALYZE the request to understand scope and depth needed
2. CREATE a structured artifact using the create_artifact tool with:
   - Appropriate artifact_type (document, code, or diagram)
   - Clear, descriptive title
   - Detailed outline with numbered sections
   - Realistic section count estimate

Guidelines for outlining:
- Each section should be self-contained and substantive
- Order sections logically (introduction → fundamentals → advanced → conclusion)
- For code artifacts, include: overview, setup, implementation, testing, deployment
- For documents, include: introduction, context, main content sections, summary
- Aim for 3-5 sections for focused, concise content (keep it brief for faster generation)

After creating the artifact outline, you will generate content section by section.

IMPORTANT: Always use the create_artifact tool when the user requests comprehensive content. Do not generate content directly without creating an artifact first."""


ARTIFACT_GENERATION_SYSTEM_PROMPT = """You are generating content for an artifact. You have access to the following tools:

- update_artifact: Append content for the current section
- finalize_artifact: Mark the artifact as complete when all sections are done

Current artifact context:
- Title: {title}
- Type: {artifact_type}
- Outline: {outline}
- Current section: {current_section} of {total_sections}
- Content so far: {content_preview}

Guidelines for section generation:
1. Generate content ONLY for the current section indicated
2. Use appropriate formatting (markdown for documents, proper syntax for code)
3. Maintain consistency with previous sections
4. Each section should be 200-500 words for documents, or complete functional code for code artifacts
5. Include relevant examples, explanations, and transitions
6. Call update_artifact with the section content and section_number

When the final section is complete, call finalize_artifact with a brief summary.

IMPORTANT: Generate one section at a time. Wait for confirmation before proceeding to the next section."""


ARTIFACT_CONTINUATION_PROMPT = """Continue generating content for the artifact.

Current state:
- Title: {title}
- Progress: Section {current_section} of {total_sections}
- Next section to generate based on outline: {next_section_title}

Resume from where you left off. Generate content for section {current_section} and use the update_artifact tool to save it."""


def get_planning_prompt() -> str:
    """
    Get the system prompt for artifact planning phase.

    Returns:
        Complete system prompt for planning
    """
    return ARTIFACT_PLANNING_SYSTEM_PROMPT


def get_generation_prompt(
    title: str,
    artifact_type: str,
    outline: str,
    current_section: int,
    total_sections: int,
    content_preview: str = ""
) -> str:
    """
    Get the system prompt for artifact generation phase.

    Args:
        title: Artifact title
        artifact_type: Type of artifact
        outline: The artifact outline
        current_section: Current section number
        total_sections: Total sections
        content_preview: Preview of content generated so far

    Returns:
        Formatted system prompt for generation
    """
    # Truncate content preview if too long
    if len(content_preview) > 500:
        content_preview = content_preview[-500:] + "..."

    return ARTIFACT_GENERATION_SYSTEM_PROMPT.format(
        title=title,
        artifact_type=artifact_type,
        outline=outline,
        current_section=current_section,
        total_sections=total_sections,
        content_preview=content_preview or "(No content yet)"
    )


def get_continuation_prompt(
    title: str,
    current_section: int,
    total_sections: int,
    next_section_title: str
) -> str:
    """
    Get the prompt for continuing artifact generation after pause.

    Args:
        title: Artifact title
        current_section: Current section to generate
        total_sections: Total sections
        next_section_title: Title of the next section from outline

    Returns:
        Formatted continuation prompt
    """
    return ARTIFACT_CONTINUATION_PROMPT.format(
        title=title,
        current_section=current_section,
        total_sections=total_sections,
        next_section_title=next_section_title
    )


def get_section_user_prompt(outline: str, section_number: int) -> str:
    """
    Get the user prompt to request a specific section.

    Args:
        outline: The artifact outline
        section_number: Section to generate

    Returns:
        User prompt for section generation
    """
    # Try to extract the section title from outline
    lines = outline.strip().split('\n')
    section_title = f"Section {section_number}"

    for line in lines:
        line = line.strip()
        # Match patterns like "1." or "1:" or "1)"
        if line.startswith(f"{section_number}.") or \
           line.startswith(f"{section_number}:") or \
           line.startswith(f"{section_number})"):
            section_title = line
            break

    return f"Please generate content for: {section_title}"


# ========== Modification (Append Sections) Prompts ==========

ARTIFACT_APPEND_PLANNING_PROMPT = """You are an expert content architect. You are ADDING new sections to an existing artifact.

## Existing Artifact Context
- Title: {title}
- Type: {artifact_type}
- Current Sections: {current_sections}
- Current Outline:
{outline}

- Content Summary (last 500 chars):
{content_preview}

## User Request
{user_message}

## Your Task
Analyze the user's request and plan NEW sections to APPEND to the end of this artifact.

Guidelines:
- Continue section numbering from {next_section_number}
- Ensure new sections complement existing content
- Don't repeat what already exists
- Keep new sections focused on the user's request
- Aim for 1-5 new sections depending on request scope

Use the append_sections tool to specify the new sections to add."""


def get_append_planning_prompt(
    title: str,
    artifact_type: str,
    outline: str,
    content_preview: str,
    current_sections: int,
    user_message: str,
) -> str:
    """
    Get the system prompt for append sections planning phase.

    Args:
        title: Existing artifact title
        artifact_type: Type of artifact
        outline: Existing outline
        content_preview: Preview of existing content
        current_sections: Number of existing sections
        user_message: User's modification request

    Returns:
        Formatted system prompt for append planning
    """
    # Pass full content for complete context (don't truncate)
    preview = content_preview if content_preview else "(No content yet)"

    return ARTIFACT_APPEND_PLANNING_PROMPT.format(
        title=title,
        artifact_type=artifact_type,
        outline=outline,
        content_preview=preview,
        current_sections=current_sections,
        next_section_number=current_sections + 1,
        user_message=user_message,
    )


ARTIFACT_APPEND_GENERATION_PROMPT = """You are generating NEW sections for an existing artifact. You have access to the following tools:

- update_artifact: Append content for the current section
- finalize_artifact: Mark the artifact as complete when all NEW sections are done

Current artifact context:
- Title: {title}
- Type: {artifact_type}
- Original sections: {original_sections}
- New sections outline: {new_sections_outline}
- Current section: {current_section} of {total_sections}
- Generating new section: {new_section_number} (section {new_section_number} of {total_new_sections} new sections)

Guidelines for section generation:
1. Generate content ONLY for the current new section indicated
2. Maintain consistency with existing content style
3. Use appropriate formatting (markdown for documents, proper syntax for code)
4. Each section should be 200-500 words for documents, or complete functional code for code artifacts
5. Call update_artifact with the section content and correct section_number
6. Remember: section numbering continues from {next_section_start}

When the final NEW section is complete, call finalize_artifact with a brief summary of what was added."""


def get_append_generation_prompt(
    title: str,
    artifact_type: str,
    original_sections: int,
    new_sections_outline: str,
    current_section: int,
    total_sections: int,
) -> str:
    """
    Get the system prompt for append sections generation phase.

    Args:
        title: Artifact title
        artifact_type: Type of artifact
        original_sections: Number of sections before modification
        new_sections_outline: Outline of NEW sections only
        current_section: Current section number (absolute, including original)
        total_sections: Total sections after modification

    Returns:
        Formatted system prompt for append generation
    """
    total_new_sections = total_sections - original_sections
    new_section_number = current_section - original_sections

    return ARTIFACT_APPEND_GENERATION_PROMPT.format(
        title=title,
        artifact_type=artifact_type,
        original_sections=original_sections,
        new_sections_outline=new_sections_outline,
        current_section=current_section,
        total_sections=total_sections,
        new_section_number=new_section_number,
        total_new_sections=total_new_sections,
        next_section_start=original_sections + 1,
    )


