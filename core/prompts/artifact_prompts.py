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


# ========== Section Rewrite Prompts ==========

ARTIFACT_SECTION_REWRITE_PROMPT = """You are rewriting a SPECIFIC section of an existing artifact.

## Existing Artifact
- Title: {title}
- Type: {artifact_type}
- Total Sections: {total_sections}
- Target Section: {target_section_number} - "{target_section_title}"

## Other Sections (for context only - do NOT include these in your output)
{other_sections_summary}

## Original Section Content to Rewrite
{original_section_content}

## User's Request
{user_message}

## Your Task
Rewrite ONLY section {target_section_number} based on the user's feedback.

Guidelines:
1. Output ONLY the rewritten section content
2. Keep the same section number ({target_section_number})
3. Maintain the same general topic/purpose
4. Apply the user's requested changes
5. Use consistent formatting with the rest of the document
6. Do NOT include other sections in your output
7. Do NOT include a section header/title - just the content

Output the rewritten section content now:"""


def get_section_rewrite_prompt(
    title: str,
    artifact_type: str,
    total_sections: int,
    target_section_number: int,
    target_section_title: str,
    original_section_content: str,
    other_sections_summary: str,
    user_message: str,
) -> str:
    """
    Get the system prompt for section rewrite.

    Args:
        title: Artifact title
        artifact_type: Type of artifact
        total_sections: Total number of sections
        target_section_number: The section number to rewrite (1-indexed)
        target_section_title: Title of the section to rewrite
        original_section_content: Current content of the target section
        other_sections_summary: Brief summary of other sections for context
        user_message: User's rewrite request

    Returns:
        Formatted system prompt for section rewrite
    """
    return ARTIFACT_SECTION_REWRITE_PROMPT.format(
        title=title,
        artifact_type=artifact_type,
        total_sections=total_sections,
        target_section_number=target_section_number,
        target_section_title=target_section_title,
        original_section_content=original_section_content,
        other_sections_summary=other_sections_summary,
        user_message=user_message,
    )


def parse_sections_from_content(content: str) -> list[dict]:
    """
    Parse artifact content into sections based on ## headers (h2).
    
    Args:
        content: The full artifact content (markdown)
        
    Returns:
        List of dicts with keys: number, title, content, start_pos, end_pos
    """
    import re
    
    sections = []
    # Match ## headers (h2 level)
    pattern = r'^##\s+(.+?)$'
    matches = list(re.finditer(pattern, content, re.MULTILINE))
    
    for i, match in enumerate(matches):
        section_title = match.group(1).strip()
        start_pos = match.end()
        
        # End position is start of next section or end of content
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(content)
        
        section_content = content[start_pos:end_pos].strip()
        
        sections.append({
            'number': i + 1,
            'title': section_title,
            'content': section_content,
            'header_start': match.start(),
            'content_start': start_pos,
            'content_end': end_pos,
        })
    
    return sections


def reconstruct_content_with_new_section(
    original_content: str,
    sections: list[dict],
    target_section_number: int,
    new_section_content: str,
) -> str:
    """
    Reconstruct the full artifact content with a rewritten section.
    
    Args:
        original_content: The original full content
        sections: Parsed sections from parse_sections_from_content
        target_section_number: Which section to replace (1-indexed)
        new_section_content: The new content for that section
        
    Returns:
        Full content with the target section replaced
    """
    if not sections or target_section_number < 1 or target_section_number > len(sections):
        return original_content
    
    target_section = sections[target_section_number - 1]
    
    # Build new content by keeping everything before and after the section content
    before = original_content[:target_section['content_start']]
    after = original_content[target_section['content_end']:]
    
    # Ensure proper spacing
    new_content = new_section_content.strip()
    if not before.endswith('\n'):
        before += '\n'
    
    return before + new_content + '\n' + after.lstrip('\n')


def extract_target_section_from_message(message: str, total_sections: int) -> int | None:
    """
    Extract the target section number from a user's rewrite request.
    
    Args:
        message: User's message (e.g., "rewrite section 2", "redo the first section")
        total_sections: Total number of sections in the artifact
        
    Returns:
        Section number (1-indexed) or None if not found
    """
    import re
    
    message_lower = message.lower()
    
    # Try to find explicit section number: "section 2", "part 3"
    match = re.search(r'(section|part)\s*(\d+)', message_lower)
    if match:
        section_num = int(match.group(2))
        if 1 <= section_num <= total_sections:
            return section_num
    
    # Ordinal references
    ordinal_map = {
        'first': 1,
        'second': 2,
        'third': 3,
        'fourth': 4,
        'fifth': 5,
        'sixth': 6,
        'seventh': 7,
        'eighth': 8,
        'ninth': 9,
        'tenth': 10,
        'last': total_sections,
    }
    
    for word, num in ordinal_map.items():
        if word in message_lower:
            if 1 <= num <= total_sections:
                return num
    
    # Default to first section if no specific section mentioned
    # This handles cases like "rewrite the introduction"
    return 1

