"""
Populate/Update public sentiment data for AI model cards into ModelCardData

Usage:
    # as a weekly cron (refreshes stale models)
    python manage.py update_model_card_public_sentiment

    # refresh one model --force ignores any existing search/analysis cache
    python manage.py update_model_card_public_sentiment --model "GPT-4o" --force

    # Load data from existing cache only
    python manage.py update_model_card_public_sentiment --stage load

    # Load all models found in cache (creates records if needed)
    python manage.py update_model_card_public_sentiment --model cached --stage load

    # Dry run
    python manage.py update_model_card_public_sentiment --dry-run

Cache(s) stored:
    JSON files in model_card_cache/public_feedback/
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from conversations.models import LLM, ModelCardData, PublicFeedbackSourceCluster, PublicFeedbackSource

# Try to import anthropic (optional - only needed for search/analysis stages)
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# Map model names to provider
PROVIDER_MAP = {
    'claude': 'Anthropic',
    'gpt': 'OpenAI',
    'dall-e': 'OpenAI',
    'whisper': 'OpenAI',
    'gemini': 'Google',
    'llama': 'Meta',
    'mistral': 'Mistral AI',
    'deepseek': 'DeepSeek',
    'granite': 'IBM',
    'olmo': 'Allen AI',
    'qwq': 'Alibaba',
    'internlm': 'Shanghai AI Lab',
}

IMAGE_MODELS = ["DALL-E 2", "DALL-E 3"]
AUDIO_MODELS = ["Whisper-1", "GPT-4o Transcribe Diarize"]


def get_provider(model_name: str) -> str:
    """Infer provider from model name."""
    name_lower = model_name.lower()
    for prefix, provider in PROVIDER_MAP.items():
        if prefix in name_lower:
            return provider
    return 'Unknown'


def get_model_type(model_name: str) -> str:
    """Classify model by type for query/prompt selection."""
    if model_name in IMAGE_MODELS:
        return "image"
    elif model_name in AUDIO_MODELS:
        return "audio"
    return "text"


def generate_slug(model_name: str) -> str:
    """Generate URL-friendly slug from model name.

    'Claude Sonnet 4.5' -> 'claude-sonnet-4-5'
    'GPT-4o Mini' -> 'gpt-4o-mini'
    """
    # Replace dots with hyphens before slugify (preserve version numbers)
    normalized = model_name.replace('.', '-')
    return slugify(normalized)


# Static variant mappings (model-specific quirks, vetted for an initial set we piloted)
VARIANT_MAP = {
    'claude-opus-4-5': ['Claude Opus 4.5', 'claude-opus-4-5', 'Claude 4.5 Opus'],
    'claude-sonnet-4-5': ['Claude Sonnet 4.5', 'claude-sonnet-4-5', 'Claude 4.5 Sonnet'],
    'claude-sonnet-3': ['Claude Sonnet 3', 'claude-sonnet-3', 'Claude 3 Sonnet'],
    'gpt-4o': ['GPT-4o', 'gpt-4o', 'GPT 4o'],
    'gpt-4o-mini': ['GPT 4o Mini', 'gpt-4o-mini', 'GPT-4o Mini'],
    'gpt-3-5': ['GPT 3.5', 'gpt-3-5', 'gpt-3.5', 'GPT-3.5', 'GPT 3.5 Turbo', 'GPT-3.5 Turbo', 'ChatGPT 3.5', 'ChatGPT-3.5'],
    'gpt-5-1': ['GPT-5.1', 'gpt-5-1', 'gpt-5.1', 'GPT 5.1'],
    'gemini-2-5-pro': ['Gemini 2.5 Pro', 'gemini-2-5-pro', 'gemini-2.5-pro', 'Gemini 2.5'],
    'gemini-3-pro': ['Gemini 3 Pro', 'gemini-3-pro', 'Gemini 3'],
}


# Search query templates by model type
TASK_QUERIES_BY_TYPE = {
    "text": [
        "{model} for research",
        "{model} for academic writing",
        "{model} for data analysis",
        "{model} for literature review",
        "{model} for coding",
        "{model} hallucination",
        "{model} accuracy",
        "{model} instruction following",
        "{model} summarization",
        "{model} classification",
        "{model} information extraction",
        "{model} OCR",
        "{model} image captioning",
    ],
    "image": [
        "{model} image quality",
        "{model} prompt accuracy",
        "{model} art style",
        "{model} photorealism",
        "{model} consistency",
    ],
    "audio": [
        "{model} transcription accuracy",
        "{model} language support",
        "{model} speaker diarization",
        "{model} background noise handling",
    ],
}

SEARCH_TIERS = {
    "general": [
        "{model} review",
        "{model} user experience",
        "{model} feedback",
        "{model} pros and cons",
    ],
    "task_specific": [],  # Populated from TASK_QUERIES_BY_TYPE
    "community": [
        "{model} reddit",
        "{model} hacker news",
        "site:reddit.com {model} review",
    ],
}

# Task insights schema varies by model type
TASK_INSIGHTS_BY_TYPE = {
    "text": "research, academic_writing, data_analysis, literature_review, coding",
    "image": "image_quality, prompt_accuracy, style_consistency, photorealism, generation_speed",
    "audio": "transcription_accuracy, language_support, speaker_identification, noise_handling, real_time_performance",
}

# Rate limiting for API calls
SEARCH_DELAY_SECONDS = 2


def generate_name_variants(model_name: str, slug: str) -> list:
    """Get name variants from static map, with fallback: generate common name variants for fuzzy matching."""

    if slug in VARIANT_MAP:
        return VARIANT_MAP[slug]

    # Fallback for unknown models
    variants = [
        model_name,
        slug,
        model_name.lower(),
        model_name.replace(' ', '-'),
        model_name.replace(' ', '_'),
        model_name.replace('.', '-'),
    ]
    # Dedupe while preserving order
    seen = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


ANALYSIS_PROMPT = """You are analyzing public reviews and user experiences about {model_name}.

Based on the clustered search results below, provide a structured analysis WITH CITATIONS.

<search_results>
{search_results}
</search_results>

IMPORTANT: Every claim must include a "refs" array citing the cluster numbers that support it.
Only make claims that are directly supported by the sources. If a claim cannot be attributed 
to at least one source, do not include it.

Analyze these results and return a JSON object with this structure:

{{
    "model_name": "{model_name}",
    "overall_sentiment": {{
        "score": <0-10 scale, 5 = neutral>,
        "label": "<very negative | negative | mixed | positive | very positive>",
        "confidence": "<low | medium | high>",
        "reasoning": "<1-2 sentence explanation>",
        "refs": [<cluster numbers supporting this assessment>]
    }},
    "key_themes": [
        {{
            "theme": "<theme name, e.g., 'Code Quality', 'Hallucination Tendency'>",
            "sentiment": "<positive | negative | mixed>",
            "frequency": "<how often mentioned: rare | occasional | frequent>",
            "example_quotes": [
                {{"quote": "<actual quote>", "ref": <cluster number>}}
             ],
            "refs": [<all cluster numbers discussing this theme>],
            "task_relevance": ["<relevant to which task categories>"]
        }}
    ],
    "strengths": [
        {{"claim": "<strength 1>", "refs": [<cluster numbers>]}},
        {{"claim": "<strength 2>", "refs": [<cluster numbers>]}}
    ],
    "weaknesses": [
        {{"claim": "<weakness 1>", "refs": [<cluster numbers>]}},
        {{"claim": "<weakness 2>", "refs": [<cluster numbers>]}}
    ],
    "task_specific_insights": {{
{task_insights_schema}
    }},
    "comparative_mentions": [
        {{
            "compared_to": "<other model name>",
            "comparison_result": "<better | worse | similar>",
            "context": "<brief explanation>",
            "refs": [<cluster numbers>]
        }}
    ],
    "metadata": {{
        "sources_analyzed": <total unique sources across all clusters>,
        "clusters_cited": <number of clusters actually referenced in your analysis>,
        "date_range": "<earliest to latest date if available>",
        "primary_sources": ["<main source types, e.g., 'Reddit', 'HackerNews'>"],
        "confidence_notes": "<any caveats about the analysis>"
    }}
}}

Evidence Standards:
- SINGLE SOURCE claims: Use hedging language ("one user reported", "according to one source")
- 2-3 SOURCES: Can state as observation ("some users report", "several sources mention")
- 4+ SOURCES: Can state as pattern ("users frequently mention", "a common theme")
- NEVER generalize a specific benchmark result to overall model quality
- NEVER conflate model versions (GPT-4 ≠ GPT-4o ≠ GPT-4 Turbo; Claude 3.5 ≠ Claude 4)

What NOT to do:
- Do not invent claims that sound plausible but aren't in the sources
- Do not merge findings from different model versions into a single assessment
- Do not convert a single user complaint into a "known issue" or "common problem"
- Do not state specific statistics (percentages, rates) unless directly quoted from a source
- If sources contradict each other, note the disagreement rather than picking a side

Important:
- EVERY strength, weakness, theme, and insight MUST have a "refs" array
- Only include claims that can be traced to specific sources
- If a task_specific_insight has no supporting sources, use {{"summary": "No data available", "refs": []}}
- Be honest about confidence levels - if data is sparse, say so
- If multiple clusters discuss the same topic, include all relevant cluster numbers
- Map insights to task categories where relevant (summarization, classification, info extraction, argument mining, domain QA, OCR, image captioning)

Return ONLY the JSON object, no additional text."""


# Task insights schema templates by model type
TASK_INSIGHTS_SCHEMA = {
    "text": """        "research": {{"summary": "<how users rate for research tasks>", "refs": [<cluster numbers>]}},
                       "academic_writing": {{"summary": "<how users rate for academic writing>", "refs": [<cluster numbers>]}},
                       "data_analysis": {{"summary": "<how users rate for data analysis>", "refs": [<cluster numbers>]}},
                       "literature_review": {{"summary": "<how users rate for literature review>", "refs": [<cluster numbers>]}},
                       "coding": {{"summary": "<how users rate for coding tasks>", "refs": [<cluster numbers>]}}""",
    "image": """        "image_quality": {{"summary": "<how users rate image quality>", "refs": [<cluster numbers>]}},
                       "prompt_accuracy": {{"summary": "<how well it follows prompts>", "refs": [<cluster numbers>]}},
                       "style_consistency": {{"summary": "<consistency across generations>", "refs": [<cluster numbers>]}},
                       "photorealism": {{"summary": "<realism of generated images>", "refs": [<cluster numbers>]}},
                       "generation_speed": {{"summary": "<speed of generation>", "refs": [<cluster numbers>]}}""",
    "audio": """        "transcription_accuracy": {{"summary": "<accuracy of transcriptions>", "refs": [<cluster numbers>]}},
                       "language_support": {{"summary": "<multi-language capabilities>", "refs": [<cluster numbers>]}},
                       "speaker_identification": {{"summary": "<speaker diarization quality>", "refs": [<cluster numbers>]}},
                       "noise_handling": {{"summary": "<background noise handling>", "refs": [<cluster numbers>]}},
                       "real_time_performance": {{"summary": "<real-time processing capability>", "refs": [<cluster numbers>]}}""",
}


@dataclass
class Source:
    """A single search result source."""
    title: str
    url: str
    snippet: str = ""
    source_type: str = "other"  # hackernews, reddit, arxiv, blog, etc.
    page_date: str | None = None
    originating_query: str = ""


@dataclass
class SourceCluster:
    """A cluster of related sources (same content, different platforms)."""
    cluster_index: int
    canonical_title: str
    canonical_url: str
    identifier: str | None  # arXiv ID, DOI, etc.
    sources: list[Source] = field(default_factory=list)

class SourceClusterer:
    """Clusters related sources to reduce redundancy and enable citations."""

    # Patterns for identifier extraction
    ARXIV_PATTERNS = [
        r'arxiv\.org/abs/(\d{4}\.\d{4,5})',
        r'arxiv\.org/pdf/(\d{4}\.\d{4,5})',
        r'\[(\d{4}\.\d{4,5})\]',
    ]
    DOI_PATTERN = r'(10\.\d{4,}/[^\s]+)'

    # URL patterns for source type detection
    SOURCE_TYPE_PATTERNS = {
        'hackernews': [r'news\.ycombinator\.com', r'hn\.algolia\.com'],
        'reddit': [r'reddit\.com', r'redd\.it'],
        'arxiv': [r'arxiv\.org'],
        'blog': [r'medium\.com', r'substack\.com', r'dev\.to', r'\.blog\.'],
        'news': [r'techcrunch\.com', r'theverge\.com', r'wired\.com', r'arstechnica\.com'],
        'twitter': [r'twitter\.com', r'x\.com'],
        'review': [r'openreview\.net', r'paperswithcode\.com'],
        'forum': [r'stackoverflow\.com', r'community\.openai\.com'],
    }

    # Title suffixes to strip for fuzzy matching
    TITLE_SUFFIXES = [
        r'\s*\|\s*Hacker News$',
        r'\s*:\s*Hacker News$',
        r'\s*\|\s*Reddit$',
        r'\s*-\s*Reddit$',
        r'\s*\(\d{4}\)$',
        r'\s*\[\d{4}\.\d+\]$',
    ]

    def _detect_source_type(self, url: str) -> str:
        """Detect source type from URL patterns."""
        url_lower = url.lower()
        for source_type, patterns in self.SOURCE_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url_lower):
                    return source_type
        return 'other'

    def _normalize_url_for_dedup(self, url: str) -> str:
        """Normalize URL for deduplication (strips query params)."""
        try:
            parsed = urlparse(url)
            normalized = urlunparse((
                parsed.scheme,
                parsed.netloc.lower().replace('www.', ''),
                parsed.path.rstrip('/'),
                '', '', ''  # params, query, fragment
            ))
            return normalized
        except Exception:
            return url.lower()

    def _extract_identifier(self, url: str, title: str) -> str | None:
        """Extract arXiv ID or DOI from URL or title."""
        text = f"{url} {title}"

        for pattern in self.ARXIV_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return f"arxiv:{match.group(1)}"

        doi_match = re.search(self.DOI_PATTERN, text)
        if doi_match:
            return f"doi:{doi_match.group(1)}"

        return None

    def _normalize_title(self, title: str) -> str:
        """Normalize title for fuzzy matching."""
        normalized = title
        for pattern in self.TITLE_SUFFIXES:
            normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)
        return ' '.join(normalized.lower().split())

    def _parse_source(self, raw: dict) -> Source:
        """Convert raw search result dict to Source object."""
        url = raw.get('source_url', '')
        return Source(
            title=raw.get('source_name', 'Unknown'),
            url=url,
            snippet=raw.get('snippet_text', ''),
            source_type=self._detect_source_type(url),
            page_date=raw.get('page_age'),
            originating_query=raw.get('originating_query', ''),
        )

    def cluster_sources(self, raw_sources: list[dict]) -> list[SourceCluster]:
        """
        Cluster raw search results into deduplicated groups.

        Three-phase approach:
        1. URL deduplication (normalized)
        2. Identifier matching (arXiv, DOI)
        3. Fuzzy title matching
        """
        # Convert to Source objects
        sources = [self._parse_source(s) for s in raw_sources]

        # Phase 1: Dedupe by normalized URL
        seen_urls: set[str] = set()
        unique_sources: list[Source] = []
        for source in sources:
            normalized_url = self._normalize_url_for_dedup(source.url)
            if normalized_url not in seen_urls:
                seen_urls.add(normalized_url)
                unique_sources.append(source)

        # Phase 2 & 3: Group by identifier, then fuzzy title
        clusters: list[SourceCluster] = []
        identifier_map: dict[str, int] = {}  # identifier -> cluster index
        title_map: dict[str, int] = {}  # normalized title -> cluster index

        for source in unique_sources:
            identifier = self._extract_identifier(source.url, source.title)
            normalized_title = self._normalize_title(source.title)

            cluster_idx = None

            # Check identifier match
            if identifier and identifier in identifier_map:
                cluster_idx = identifier_map[identifier]

            # Check fuzzy title match
            if cluster_idx is None:
                for existing_title, idx in title_map.items():
                    if self._titles_match(normalized_title, existing_title):
                        cluster_idx = idx
                        break

            if cluster_idx is not None:
                # Add to existing cluster
                clusters[cluster_idx].sources.append(source)
            else:
                # Create new cluster
                cluster_idx = len(clusters)
                clusters.append(SourceCluster(
                    cluster_index=cluster_idx + 1,  # 1-indexed for citations
                    canonical_title=source.title,
                    canonical_url=source.url,
                    identifier=identifier,
                    sources=[source],
                ))

                if identifier:
                    identifier_map[identifier] = cluster_idx
                title_map[normalized_title] = cluster_idx

        return clusters

    def _titles_match(self, title1: str, title2: str, threshold: float = 0.75) -> bool:
        """Check if two normalized titles are similar enough to cluster."""
        from difflib import SequenceMatcher
        return SequenceMatcher(None, title1, title2).ratio() >= threshold

    def format_for_prompt(self, clusters: list[SourceCluster]) -> str:
        """Format clusters for inclusion in analysis prompt."""
        lines = []
        for cluster in clusters:
            # Header with identifier if present
            if cluster.identifier:
                lines.append(f"[{cluster.cluster_index}] \"{cluster.canonical_title}\" ({cluster.identifier})")
            else:
                # Extract domain for context
                try:
                    domain = urlparse(cluster.canonical_url).netloc.replace('www.', '')
                    lines.append(f"[{cluster.cluster_index}] \"{cluster.canonical_title}\" ({domain})")
                except:
                    lines.append(f"[{cluster.cluster_index}] \"{cluster.canonical_title}\"")

            # Source types
            source_types = sorted(set(self._source_type_label(s.source_type) for s in cluster.sources))
            lines.append(f"    Sources: {', '.join(source_types)}")

            # Each source with full detail
            for source in cluster.sources:
                lines.append(f"")
                lines.append(f"    **{source.title}**")
                if source.url:
                    lines.append(f"    {source.url}")
                if source.snippet:
                    lines.append(f"    {source.snippet}")

            lines.append("")

        return "\n".join(lines)

    def _source_type_label(self, source_type: str) -> str:
        """Human-readable label for source type."""
        labels = {
            'hackernews': 'Hacker News discussion',
            'reddit': 'Reddit discussion',
            'arxiv': 'arXiv paper',
            'blog': 'blog post',
            'news': 'news article',
            'twitter': 'Twitter/X',
            'review': 'review/comments',
            'forum': 'forum post',
            'other': 'web page',
        }
        return labels.get(source_type, source_type)

    def format_clusters_as_json(self, clusters: list[SourceCluster]) -> list[dict]:
        """Convert clusters to JSON-serializable format for DB import."""
        return [
            {
                'cluster_index': c.cluster_index,
                'canonical_title': c.canonical_title,
                'canonical_url': c.canonical_url,
                'identifier': c.identifier,
                'sources': [
                    {
                        'title': s.title,
                        'url': s.url,
                        'source_type': s.source_type,
                        'page_date': s.page_date,
                        'snippet': s.snippet,
                        'originating_query': s.originating_query,
                    }
                    for s in c.sources
                ]
            }
            for c in clusters
        ]

# ============================================================================
# Management Command
# ============================================================================

class Command(BaseCommand):
    help = 'Crawl, analyze, and load public sentiment for AI model cards'

    def add_arguments(self, parser):
        parser.add_argument(
             '--model',
            type=str,
            default='tracked',
            help='"tracked" (all ModelCardData), "live" (set of LLMs configured in Django), "cached" (scan cache dir), or a specific model name'
        )

        # Pipeline stage
        parser.add_argument(
            '--stage',
            choices=['search', 'analyze', 'load', 'full'],
            default='full',
            help='Pipeline stage to run (default: full)'
        )

        # Cache location
        parser.add_argument(
            '--cache-dir',
            type=str,
            default='model_card_cache/public_feedback',
            help='Directory for JSON cache'
        )

        # Cache age thresholds
        parser.add_argument(
            '--max-age-days',
            type=int,
            default=7,
            help='Max cache age for live models in days (default: 7)'
        )
        parser.add_argument(
            '--max-age-days-tracked',
            type=int,
            default=90,
            help='Max cache age for tracked-only models in days (default: 90)'
        )

        # Control flags
        parser.add_argument(
            '--force',
            action='store_true',
            help='Ignore cache age, always refresh'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without executing'
        )
        parser.add_argument(
            '--backend',
            choices=['anthropic', 'dare-workflow'],
            default='anthropic',
            help='Search/analysis backend (default: anthropic)'
        )
        parser.add_argument(
            '--skip-clusters',
            action='store_true',
            help='Skip loading cluster/source data',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('Update Model Cards: Public Sentiment'))
        self.stdout.write(f"  Model selection: {options['model']}")
        self.stdout.write(f"  Stage: {options['stage']}")
        self.stdout.write(f"  Cache dir: {options['cache_dir']}")
        self.stdout.write(f"  Max age (live): {options['max_age_days']} days")
        self.stdout.write(f"  Max age (tracked): {options['max_age_days_tracked']} days")
        self.stdout.write(f"  Force: {options['force']}")
        self.stdout.write(f"  Dry run: {options['dry_run']}")
        self.stdout.write(f"  Backend: {options['backend']}")
        self.stdout.write('')

        models = self.resolve_models(options['model'], Path(options['cache_dir']))

        if options['dry_run']:
            self.stdout.write(f"Would process {len(models)} model(s):")
            for name, is_live in models:
                status = "live" if is_live else "tracked-only"
                self.stdout.write(f"  - {name} ({status})")
            return

        cache_dir = Path(options['cache_dir'])
        cache_dir.mkdir(parents=True, exist_ok=True)
        stage = options['stage']
        skip_clusters = options['skip_clusters']

        # Process each model
        for model_name, is_live in models:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\nProcessing: {model_name}'))

            # Check cache freshness, when stage=='load' we expect to load regardless of age
            if not options['force'] and stage != 'load':
                max_age = options['max_age_days'] if is_live else options['max_age_days_tracked']
                if self.is_cache_fresh(model_name, cache_dir, max_age):
                    self.stdout.write(self.style.WARNING(f'  Cache fresh (<{max_age} days), skipping'))
                    continue

            # Run requested stages
            if stage in ('search', 'full'):
                self.search_stage(model_name, cache_dir, options)

            if stage in ('analyze', 'full'):
                self.analyze_stage(model_name, cache_dir, options)

            if stage in ('load', 'full'):
                self.load_stage(model_name, cache_dir, options)

        self.stdout.write(self.style.SUCCESS('\nDone.'))


    def get_safe_filename(self, model_name: str) -> str:
        """Convert model name to cache filename prefix."""
        return model_name.replace(" ", "_").replace(".", "_").replace("/", "_").lower()


    def is_cache_fresh(self, model_name: str, cache_dir: Path, max_age_days: int) -> bool:
        """Check if analysis cache exists and is fresh enough."""
        safe_name = self.get_safe_filename(model_name)
        analysis_file = cache_dir / f"{safe_name}_public_reviews.json"

        if not analysis_file.exists():
            return False

        try:
            with open(analysis_file) as f:
                data = json.load(f)

            # Check analysis_date field
            analysis_date_str = data.get('analysis_date')
            if not analysis_date_str:
                return False

            analysis_date = datetime.fromisoformat(analysis_date_str)
            age = datetime.now() - analysis_date
            return age < timedelta(days=max_age_days)

        except (json.JSONDecodeError, KeyError, ValueError):
            return False


    def search_stage(self, model_name: str, cache_dir: Path, options: dict):
        """Run web search for model reviews."""
        self.stdout.write(f'  [SEARCH] Running search for {model_name}...')

        if not HAS_ANTHROPIC:
            self.stderr.write(self.style.ERROR('    anthropic package not installed'))
            return False

        if not os.environ.get("CLAUDE_API_KEY"):
            self.stderr.write(self.style.ERROR('    CLAUDE_API_KEY not set'))
            return False

        safe_name = self.get_safe_filename(model_name)

        # Sibling directories to public_feedback
        base_cache = cache_dir.parent
        searches_dir = base_cache / "searches" / safe_name
        aggregated_dir = base_cache / "aggregated"
        searches_dir.mkdir(parents=True, exist_ok=True)
        aggregated_dir.mkdir(parents=True, exist_ok=True)

        # Build query list from active tiers
        model_type = get_model_type(model_name)
        active_tiers = {}
        for tier_name, queries in SEARCH_TIERS.items():
            if tier_name == "task_specific":
                active_tiers[tier_name] = TASK_QUERIES_BY_TYPE.get(model_type, [])
            else:
                active_tiers[tier_name] = queries

        total_queries = sum(len(q) for q in active_tiers.values())
        query_count = 0
        all_results = {
            "model_name": model_name,
            "collection_date": datetime.now().isoformat(),
            "tiers": {}
        }

        client = anthropic.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))

        for tier_name, query_templates in active_tiers.items():
            self.stdout.write(f'    {tier_name.upper()}')
            tier_results = []

            for template in query_templates:
                query_count += 1
                query = template.format(model=model_name)

                # Check cache
                cache_key = self.get_safe_filename(f"{tier_name}_{template}")
                cache_file = searches_dir / f"{cache_key}.json"

                if not options['force'] and cache_file.exists():
                    self.stdout.write(f'      [{query_count}/{total_queries}] CACHED: {query}')
                    with open(cache_file) as f:
                        tier_results.append(json.load(f))
                    continue

                self.stdout.write(f'      [{query_count}/{total_queries}] FETCHING: {query}')

                # Execute search
                result = self._execute_search(client, query, model_name)

                # Cache result
                with open(cache_file, 'w') as f:
                    json.dump(result, f, indent=2)
                tier_results.append(result)

                # Rate limiting
                time.sleep(SEARCH_DELAY_SECONDS)

            all_results["tiers"][tier_name] = tier_results

        # Save aggregated results
        aggregated_file = aggregated_dir / f"{safe_name}_all_searches.json"
        with open(aggregated_file, 'w') as f:
            json.dump(all_results, f, indent=2)

        total_snippets = sum(
            r.get("search_results", {}).get("sources_found", 0)
            for tier in all_results["tiers"].values()
            for r in tier
        )

        self.stdout.write(self.style.SUCCESS(f'    Completed {query_count} searches, {total_snippets} sources'))
        return True


    def _execute_search(self, client, query: str, model_name: str) -> dict:
        """Execute a single web search via Anthropic API."""
        search_prompt = f"""Search the web for user reviews and experiences about {model_name}.

Query: {query}

Find real user opinions, reviews, and experiences from sources like:
- Reddit (r/LocalLLaMA, r/ChatGPT, r/ClaudeAI, etc.)
- Hacker News discussions
- Tech blogs and review sites
- Twitter/X posts
- Developer forums

For each relevant source you find, extract:
SOURCE: [exact title]
URL: [exact url]
SUMMARY: [2-3 sentence summary of the key opinions/experiences from this source]
SENTIMENT: [positive/negative/mixed]
DATE: [date if visible, otherwise "unknown", or an approximate date if one can be determined]

Focus on authentic user experiences, not marketing content. Provide at least 5-10 sources if available."""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search"
                }],
                messages=[{
                    "role": "user",
                    "content": search_prompt
                }]
            )

            # Parse response
            snippets = []
            raw_text_blocks = []

            for block in response.content:
                if block.type == "text":
                    raw_text_blocks.append(block.text)
                elif block.type == "web_search_tool_result":
                    if hasattr(block, 'content') and block.content:
                        for item in block.content:
                            if hasattr(item, 'type') and item.type == "web_search_result":
                                snippets.append({
                                    "source_name": getattr(item, 'title', 'Unknown'),
                                    "source_url": getattr(item, 'url', ''),
                                    "snippet_text": getattr(item, 'snippet', ''),
                                    "page_age": getattr(item, 'page_age', None)
                                })

            return {
                "query": query,
                "model_context": model_name,
                "timestamp": datetime.now().isoformat(),
                "search_method": "anthropic_web_search",
                "search_results": {
                    "sources_found": len(snippets),
                    "snippets": snippets,
                    "summary": "\n".join(raw_text_blocks) if raw_text_blocks else ""
                }
            }

        except Exception as e:
            self.stderr.write(self.style.WARNING(f'      Search failed: {e}'))
            return {
                "query": query,
                "model_context": model_name,
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "search_results": {"sources_found": 0, "snippets": []}
            }


    def analyze_stage(self, model_name: str, cache_dir: Path, options: dict):
        """Run LLM analysis on search results."""
        self.stdout.write(f'  [ANALYZE] Running analysis for {model_name}...')

        if not HAS_ANTHROPIC:
            self.stderr.write(self.style.ERROR('    anthropic package not installed'))
            return False

        if not os.environ.get("CLAUDE_API_KEY"):
            self.stderr.write(self.style.ERROR('    CLAUDE_API_KEY not set'))
            return False

        safe_name = self.get_safe_filename(model_name)

        # Sibling directories to public_feedback
        base_cache = cache_dir.parent
        aggregated_dir = base_cache / "aggregated"
        aggregated_file = aggregated_dir / f"{safe_name}_all_searches.json"

        if not aggregated_file.exists():
            self.stderr.write(self.style.ERROR(f'    No aggregated searches found: {aggregated_file}'))
            self.stderr.write(self.style.ERROR(f'    Run search stage first'))
            return False

        # Load and cluster search results
        search_text, clusters = self._aggregate_search_results(model_name, aggregated_file)

        self.stdout.write(f'    Clustered into {len(clusters)} clusters')

        # Build analysis prompt with model-appropriate task insights
        model_type = get_model_type(model_name)
        task_schema = TASK_INSIGHTS_SCHEMA.get(model_type, TASK_INSIGHTS_SCHEMA["text"])
        prompt = ANALYSIS_PROMPT.format(
            model_name=model_name,
            search_results=search_text,
            task_insights_schema=task_schema
        )

        # Run analysis
        self.stdout.write(f'    Sending to Claude for analysis...')
        client = anthropic.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Extract text response
            response_text = ""
            for block in response.content:
                if block.type == "text":
                    response_text += block.text

            # Parse JSON from response
            json_text = response_text.strip()
            if json_text.startswith("```json"):
                json_text = json_text[7:]
            if json_text.startswith("```"):
                json_text = json_text[3:]
            if json_text.endswith("```"):
                json_text = json_text[:-3]
            json_text = json_text.strip()

            analysis = json.loads(json_text)
            analysis["analysis_date"] = datetime.now().isoformat()
            analysis["analysis_method"] = "anthropic_claude_sonnet"

        except json.JSONDecodeError as e:
            self.stderr.write(self.style.ERROR(f'    JSON parse error: {e}'))
            analysis = {
                "model_name": model_name,
                "analysis_date": datetime.now().isoformat(),
                "error": f"JSON parse error: {e}",
                "overall_sentiment": {
                    "score": 5,
                    "label": "unknown",
                    "confidence": "low",
                    "reasoning": "Analysis failed due to parsing error"
                }
            }
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'    Analysis failed: {e}'))
            analysis = {
                "model_name": model_name,
                "analysis_date": datetime.now().isoformat(),
                "error": str(e),
                "overall_sentiment": {
                    "score": 5,
                    "label": "unknown",
                    "confidence": "low",
                    "reasoning": f"Analysis failed: {e}"
                }
            }

        # Save analysis to public_feedback (final output)
        output_file = cache_dir / f"{safe_name}_public_reviews.json"
        with open(output_file, 'w') as f:
            json.dump(analysis, f, indent=2)

        # Save clusters
        clusterer = SourceClusterer()
        clusters_json = clusterer.format_clusters_as_json(clusters)
        clusters_file = cache_dir / f"{safe_name}_clusters.json"
        with open(clusters_file, 'w') as f:
            json.dump({
                "model_name": model_name,
                "generated_at": datetime.now().isoformat(),
                "cluster_count": len(clusters),
                "clusters": clusters_json
            }, f, indent=2)

        self.stdout.write(self.style.SUCCESS(f'    Analysis saved: {output_file.name}'))
        self.stdout.write(self.style.SUCCESS(f'    Clusters saved: {clusters_file.name}'))
        return True


    def _aggregate_search_results(self, model_name: str, aggregated_file: Path) -> tuple[str, list]:
        """Load aggregated searches and format for LLM analysis."""
        with open(aggregated_file) as f:
            data = json.load(f)

        # Collect all raw sources
        all_sources = []
        query_summaries = []

        for tier_name, tier_results in data.get('tiers', {}).items():
            for result in tier_results:
                query = result.get('query', '')
                search_data = result.get('search_results', {})
                snippets = search_data.get('snippets', [])

                # Collect summary if present
                summary = search_data.get('summary', '')
                if summary:
                    query_summaries.append({
                        'query': query,
                        'summary': summary
                    })

                for snippet in snippets:
                    snippet['originating_query'] = query
                    all_sources.append(snippet)

        self.stdout.write(f'    Found {len(all_sources)} raw sources')

        # Run clustering
        clusterer = SourceClusterer()
        clusters = clusterer.cluster_sources(all_sources)

        total_unique = sum(len(c.sources) for c in clusters)
        self.stdout.write(f'    {total_unique} unique sources after dedup')

        # Format clusters for prompt
        prompt_text = clusterer.format_for_prompt(clusters)

        # Format summaries section
        summaries_text = ""
        if query_summaries:
            summaries_text = "\n## Search Summaries by Query\n\n"
            for qs in query_summaries:
                summaries_text += f"**Query: {qs['query']}**\n"
                summaries_text += f"{qs['summary']}\n\n"

        # Build header
        header = f"""# Source Clusters for {model_name}

Collection Date: {data.get('collection_date', 'unknown')}
Total clusters: {len(clusters)}
Total unique sources: {total_unique}

When analyzing, cite sources using bracket notation [1], [2], etc.
Each cluster number corresponds to the sources listed below.

---

## Clustered Sources

"""
        formatted_text = header + prompt_text + "\n" + summaries_text

        return formatted_text, clusters


    def load_stage(self, model_name: str, cache_dir: Path, options: dict):
        """Load analysis results into database for a single model."""
        self.stdout.write(f'  [LOAD] Loading {model_name} to database...')
        if not cache_dir.exists():
            self.stderr.write(self.style.ERROR(f'    Cache directory not found: {cache_dir}'))
            return False

        safe_name = self.get_safe_filename(model_name)
        json_file = cache_dir / f"{safe_name}_public_reviews.json"

        try:
            with open(json_file, 'r') as f:
                data = json.load(f)

            # Validate
            cached_model_name = data.get('model_name')
            if not cached_model_name:
                self.stderr.write(self.style.ERROR(f'    Missing model_name in {json_file.name}'))
                return False

            slug = generate_slug(cached_model_name)
            provider = get_provider(cached_model_name)
            variants = generate_name_variants(cached_model_name, slug)

            self.stdout.write(f'  Name: {cached_model_name}')
            self.stdout.write(f'  Slug: {slug}')
            self.stdout.write(f'  Provider: {provider}')
            self.stdout.write(f'  Variants: {variants}')

            if options['dry_run']:
                self.stdout.write(self.style.WARNING('  [DRY RUN] Would save'))
                return True

            # Upsert ModelCardData
            obj, was_created = ModelCardData.objects.update_or_create(
                slug=slug,
                defaults={
                    'name': cached_model_name,
                    'provider_name': provider,
                    'name_variants': variants,
                    'public_feedback': data,
                }
            )

            if was_created:
                self.stdout.write(self.style.SUCCESS(f'  Created: {obj}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'  Updated: {obj}'))

            # Load clusters if available
            if not options['skip_clusters']:
                clusters_file = json_file.parent / f"{json_file.stem.replace('_public_reviews', '')}_clusters.json"
                if clusters_file.exists():
                    cluster_count = self.load_clusters(obj, clusters_file)
                else:
                    self.stdout.write(f'  No clusters file found: {clusters_file.name}')
            return True

        except json.JSONDecodeError as e:
            self.stderr.write(self.style.ERROR(f'  JSON error: {e}'))
            return False
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'  Error: {e}'))
            return False


    def resolve_models(self, model_arg: str, cache_dir: Path = None) -> list:
        """Resolve model argument to list of model tuples of the form (name, is_live)."""
        if model_arg == 'tracked':
            # All ModelCardData records
            return [
                (mc.name, mc.llm_id is not None)
                for mc in ModelCardData.objects.all()
            ]

        elif model_arg == 'live':
            # Bootstrap ModelCardData from active LLMs (handles cold start)
            models = []
            for llm in LLM.objects.filter(is_active=True):
                mc, created = ModelCardData.objects.get_or_create(
                    llm=llm,
                    defaults={'name': llm.name, 'slug': slugify(llm.name)}
                )
                models.append((mc.name, True))
            return models

        elif model_arg == 'cached':
            # scan cache directory for model data to load
            if not cache_dir or not cache_dir.exists():
                return []

            models = []
            for json_file in cache_dir.glob('*_public_reviews.json'):
                try:
                    with open(json_file) as f:
                        data = json.load(f)
                    model_name = data.get('model_name')
                    if model_name:
                        # Check if it's linked to operational LLM
                        try:
                            mc = ModelCardData.objects.get(name__iexact=model_name)
                            is_live = mc.llm_id is not None
                        except ModelCardData.DoesNotExist:
                            is_live = False
                        models.append((model_name, is_live))
                except (json.JSONDecodeError, IOError):
                    continue
            return models

        else:
            # Specific model name - check if it exists and is live
            try:
                mc = ModelCardData.objects.get(name__iexact=model_arg)
                is_live = mc.llm_id is not None
            except ModelCardData.DoesNotExist:
                is_live = False  # New model, will create record
            return [(model_arg, is_live)]


    def load_clusters(self, model_card: ModelCardData, clusters_file: Path) -> int:
        """Load clusters and sources from JSON file into database."""

        with open(clusters_file, 'r') as f:
            data = json.load(f)

        clusters_data = data.get('clusters', [])

        if not clusters_data:
            self.stdout.write(f'  No clusters in {clusters_file.name}')
            return 0

        # Clear existing clusters for this model card
        deleted_count, _ = model_card.source_clusters.all().delete()
        if deleted_count:
            self.stdout.write(f'  Deleted {deleted_count} existing clusters')

        # Create new clusters
        for cluster_data in clusters_data:
            cluster = PublicFeedbackSourceCluster.objects.create(
                model_card=model_card,
                cluster_index=cluster_data['cluster_index'],
                canonical_title=cluster_data['canonical_title'][:500],
                canonical_url=cluster_data['canonical_url'],
                identifier=cluster_data.get('identifier') or '',
            )

            # Create sources for this cluster
            for source_data in cluster_data.get('sources', []):
                PublicFeedbackSource.objects.create(
                    cluster=cluster,
                    title=source_data['title'][:500],
                    url=source_data['url'],
                    source_type=source_data.get('source_type', 'other'),
                    page_date=source_data.get('page_date') or '',
                    snippet=source_data.get('snippet', ''),
                    originating_query=source_data.get('originating_query', ''),
                )

        self.stdout.write(self.style.SUCCESS(f'  Loaded {len(clusters_data)} clusters'))
        return len(clusters_data)
