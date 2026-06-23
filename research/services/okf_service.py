"""
OKF export — serialize a research project's DURABLE knowledge into a Google
Open Knowledge Format (OKF v0.1) bundle.

A bundle is a directory of markdown files with YAML frontmatter; markdown links
between files form the graph (spec: github.com/GoogleCloudPlatform/knowledge-catalog,
okf/SPEC.md). We export only the durable layer:

- ResearchProjectMemory  -> theses/*.md   (the "knowledge": working theses,
                                            open questions, decisions)
- ResearchKnowledgeItem  -> sources/*.md  (the supporting evidence)

Staging items are never exported — the Hermes -> staging -> scholar-promote
invariant holds. Everything here is a deterministic read + serialize of data
DARE already stores: no LLM judgement, no new tables.

A project is collected once into concept dicts (`_collect`); two consumers share
them: `build_okf_bundle` renders the zip (markdown files), `build_okf_view`
returns structured JSON for the in-app Maps viewer (file tree + rendered body +
link graph). Keeping one collector keeps the two surfaces in lockstep.

Source -> source `cites` links reuse the evidence graph's observed-citation
signal: a source's fetched full text (the GatewayFetch corpus) containing
another source's DOI. Thesis -> source links are intentionally NOT fabricated —
no such relationship exists in the schema yet.
"""

import re

from mcp.models import GatewayFetch
from research.models import (
    ResearchKnowledgeItem,
    ResearchProjectMemory,
    ResearchThesisSource,
)

THESIS_DIR = "theses"
SOURCE_DIR = "sources"


def _slug(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (slug or fallback)[:60]


def _first_sentence(text, limit=200):
    text = " ".join((text or "").split())
    if not text:
        return ""
    match = re.search(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence[:limit].rstrip()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _iso_date(dt):
    return dt.strftime("%Y-%m-%d") if dt else "undated"


def _yaml_scalar(value):
    """Double-quote a string scalar, escaped for YAML — safe for any content."""
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _render_fm(fields):
    """fields: list of (key, value, kind). kind in {str, list, num, raw}. Returns
    (yaml_block, clean_map): the rendered `---` block and the non-empty values as
    a dict (for JSON). `raw` is emitted unquoted (ISO timestamps)."""
    lines = ["---"]
    clean = {}
    for key, value, kind in fields:
        if value in (None, "", [], {}):
            continue
        clean[key] = value
        if kind == "list":
            rendered = "[" + ", ".join(_yaml_scalar(v) for v in value) + "]"
        elif kind in ("num", "raw"):
            rendered = str(value)
        else:
            rendered = _yaml_scalar(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines), clean


def _thesis_type(label):
    """Map a ResearchProjectMemory label to an OKF `type` (free-form per spec;
    bucketed on keywords, with a generic fallback)."""
    text = (label or "").lower()
    if "question" in text:
        return "Open question"
    if "decision" in text or "decide" in text:
        return "Decision"
    if "thesis" in text or "claim" in text or "hypothes" in text:
        return "Working thesis"
    return "Research note"


def _source_biblio(ki):
    """Bibliographic fields for a promoted source live on the staging item it was
    promoted from; the durable row carries content/rationale/provenance."""
    return ki.source_staging_item


def _source_resource(ki, si):
    if si and si.doi:
        return si.doi if si.doi.startswith("http") else f"https://doi.org/{si.doi}"
    if si and si.url:
        return si.url
    return f"urn:dare:research:{ki.project_id}:source:{ki.id}"


def _norm_doi(doi):
    d = (doi or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.strip("/")


def _norm_url(url):
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.split("?")[0].split("#")[0].rstrip("/")


def _norm_text(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _first_surname(authors):
    """Best-effort first-author surname: the longest alphabetic token (>= 3) in
    the first author chunk (handles 'Macnamara S', 'B. N. Macnamara', etc.)."""
    first = re.split(r"[;,]| and ", (authors or "").strip())[0]
    tokens = re.findall(r"[a-zA-Z]{3,}", first)
    return max(tokens, key=len).lower() if tokens else ""


def _author_year_near(raw, surname, year, window=80):
    idx = raw.find(surname)
    while idx != -1:
        if year in raw[max(0, idx - window) : idx + len(surname) + window]:
            return True
        idx = raw.find(surname, idx + 1)
    return False


def _citation_method(target_si, raw, norm):
    """How (if at all) the target source is cited inside a citing source's text.
    Conservative, observed-only — DOI substring, then a verbatim (normalized)
    title, then a first-author-surname + year co-occurrence. '' = no match."""
    nd = _norm_doi(target_si.doi)
    if nd and nd in raw:
        return "doi"
    nt = _norm_text(target_si.title)
    if len(nt) >= 25 and nt in norm:
        return "title"
    surname = _first_surname(target_si.authors)
    year = str(target_si.year) if target_si.year else ""
    if surname and year and _author_year_near(raw, surname, year):
        return "author-year"
    return ""


def _source_corpus(project, kis):
    """ki.id -> (raw_lowercased_text, alnum_normalized_text) for every source
    whose page is in the gateway corpus, matched by NORMALIZED url or doi — so
    redirects, doi.org links, www and trailing slashes don't hide a fetched
    page (the URL-mismatch gap)."""
    fetches = GatewayFetch.active_objects.filter(user=project.user, tool="fetch_page")
    by_url, by_doi = {}, {}
    for f in fetches:
        if not f.content:
            continue
        if f.url:
            by_url.setdefault(_norm_url(f.url), f.content)
            doi_from_url = _norm_doi(f.url)
            if doi_from_url:
                by_doi.setdefault(doi_from_url, f.content)
        if f.doi:
            by_doi.setdefault(_norm_doi(f.doi), f.content)
    corpus = {}
    for ki in kis:
        si = ki.source_staging_item
        if not si:
            continue
        content = None
        if si.url:
            content = by_url.get(_norm_url(si.url)) or by_doi.get(_norm_doi(si.url))
        if content is None and si.doi:
            content = by_doi.get(_norm_doi(si.doi))
        if content:
            low = content.lower()
            corpus[ki.id] = (low, _norm_text(low))
    return corpus


def _citation_links(project, kis):
    """ki.id -> list of (cited_ki_id, cited_title, via). Observed citations only,
    but matched three ways — DOI, verbatim title, or first-author + year — so
    real references expressed as author-year (not raw DOIs) are still caught."""
    corpus = _source_corpus(project, kis)
    targets = [(ki, ki.source_staging_item) for ki in kis if ki.source_staging_item]
    cites = {}
    for src_id, (raw, norm) in corpus.items():
        for tgt, tgt_si in targets:
            if tgt.id == src_id:
                continue
            via = _citation_method(tgt_si, raw, norm)
            if via:
                cites.setdefault(src_id, []).append(
                    (tgt.id, tgt_si.title or f"Source {tgt.id}", via)
                )
    return cites


def _thesis_path(mem):
    return f"{THESIS_DIR}/{_slug(mem.label, f'note-{mem.id}')}-{mem.id}.md"


def _source_path(ki):
    return f"{SOURCE_DIR}/source-{ki.id}.md"


def _source_title(ki):
    si = ki.source_staging_item
    return (si.title if si else "") or f"Source {ki.id}"


def _group_by_stance(items):
    """items: list of (obj, stance) -> {stance_lower: [obj, ...]}."""
    groups = {}
    for obj, stance in items:
        groups.setdefault((stance or "").lower(), []).append(obj)
    return groups


# (stance key, heading on the thesis file, heading on the source file)
_STANCE_SECTIONS = [
    ("supporting", "Supported by", "Supports"),
    ("disputing", "Disputed by", "Disputes"),
    ("partial", "Partially supported by", "Partially supports"),
]


def _thesis_concept(mem, field, linked_sources):
    kind = _thesis_type(mem.label)
    title = mem.label or f"Note {mem.id}"
    description = _first_sentence(mem.detail)
    path = _thesis_path(mem)
    yaml, fm = _render_fm(
        [
            ("type", kind, "str"),
            ("title", title, "str"),
            ("description", description, "str"),
            ("resource", f"urn:dare:research:{mem.project_id}:memory:{mem.id}", "str"),
            ("tags", [field] if field else [], "list"),
            ("timestamp", _iso(mem.updated_at or mem.created_at), "raw"),
            ("origin", mem.source, "str"),
        ]
    )
    body = ["# " + title, mem.detail or "_No detail recorded._"]
    links = []
    groups = _group_by_stance(linked_sources)
    for stance_key, thesis_heading, _ in _STANCE_SECTIONS:
        srcs = groups.pop(stance_key, [])
        if not srcs:
            continue
        body += ["", f"# {thesis_heading}"]
        for src in srcs:
            sp, stitle = _source_path(src), _source_title(src)
            body.append(f"- [{stitle}](/{sp})")
            links.append({"to": sp[:-3], "text": stitle, "kind": stance_key})
    leftover = [s for group in groups.values() for s in group]
    if leftover:
        body += ["", "# Related sources"]
        for src in leftover:
            sp, stitle = _source_path(src), _source_title(src)
            body.append(f"- [{stitle}](/{sp})")
            links.append({"to": sp[:-3], "text": stitle, "kind": "related"})
    return {
        "path": path,
        "conceptId": path[:-3],
        "kind": "thesis",
        "type": kind,
        "title": title,
        "description": description,
        "frontmatter": fm,
        "yaml": yaml,
        "body": "\n".join(body),
        "links": links,
        "evidenceLabel": "",
        "confidence": None,
        "log": (_iso_date(mem.created_at), f"- **{kind}** scholar recorded: {title}"),
    }


def _source_concept(ki, cites, linked_theses):
    si = _source_biblio(ki)
    title = _source_title(ki)
    label = (si.evidence_label if si else "") or ""
    confidence = round(si.confidence, 2) if si and si.confidence is not None else None
    description = _first_sentence(ki.content) or (si.abstract if si else "")
    path = _source_path(ki)
    tags = ([label] if label else []) + list(ki.used_in or [])
    yaml, fm = _render_fm(
        [
            ("type", "Research Source", "str"),
            ("title", title, "str"),
            ("description", description, "str"),
            ("resource", _source_resource(ki, si), "str"),
            ("tags", tags, "list"),
            ("timestamp", _iso(ki.approved_at or ki.created_at), "raw"),
            ("confidence", confidence, "num"),
            ("evidence_label", label, "str"),
            ("soul_file_version", ki.soul_file_version, "str"),
        ]
    )
<<<<<<< HEAD
    body = [
        "# Finding",
        ki.content or (si.abstract if si else "") or "_No content recorded._",
    ]
=======
    body = ["# Finding", ki.content or (si.abstract if si else "") or "_No content recorded._"]
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
    if ki.rationale:
        body += ["", "# Why it is in the record", ki.rationale]
    ref = []
    if si:
        if si.authors:
            ref.append(f"Authors: {si.authors}")
        if si.year:
            ref.append(f"Year: {si.year}")
        if si.venue:
            ref.append(f"Venue: {si.venue}")
    if ref:
        body += ["", "# Reference", " · ".join(ref)]
    links = []
    if cites:
        body += ["", "# Cites"]
        for cid, text, via in cites:
            body.append(f"- [{text}](/{SOURCE_DIR}/source-{cid}.md)")
            links.append(
<<<<<<< HEAD
                {
                    "to": f"{SOURCE_DIR}/source-{cid}",
                    "text": text,
                    "kind": "cites",
                    "via": via,
                }
=======
                {"to": f"{SOURCE_DIR}/source-{cid}", "text": text, "kind": "cites", "via": via}
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
            )
    # Backlinks to the theses this source bears on. Body-only — the graph edge is
    # emitted once, from the thesis side, to avoid duplicate links.
    groups = _group_by_stance(linked_theses)
    for stance_key, _, source_heading in _STANCE_SECTIONS:
        ths = groups.pop(stance_key, [])
        if not ths:
            continue
        body += ["", f"# {source_heading}"]
        for th in ths:
            body.append(f"- [{th.label or f'Note {th.id}'}](/{_thesis_path(th)})")
    leftover = [t for group in groups.values() for t in group]
    if leftover:
        body += ["", "# Bears on"]
        for th in leftover:
            body.append(f"- [{th.label or f'Note {th.id}'}](/{_thesis_path(th)})")
    return {
        "path": path,
        "conceptId": path[:-3],
        "kind": "source",
        "type": "Research Source",
        "title": title,
        "description": description,
        "frontmatter": fm,
        "yaml": yaml,
        "body": "\n".join(body),
        "links": links,
        "evidenceLabel": label,
        "confidence": confidence,
        "log": (
            _iso_date(ki.approved_at or ki.created_at),
            f"- **Source** scholar promoted: {title} ({label or 'source'})",
        ),
    }


def _index_concept(project, theses, sources):
    lines = [f"# {project.question or project.title or 'Research project'}"]
    intro = "Durable knowledge promoted by the scholar."
    if project.field:
        intro += f" Field: {project.field}."
    lines += ["", intro]
    if theses:
        lines += ["", "## Theses and open questions"]
<<<<<<< HEAD
        lines += [
            f"- [{c['title']}](/{c['path']}) — {c['type'].lower()}" for c in theses
        ]
=======
        lines += [f"- [{c['title']}](/{c['path']}) — {c['type'].lower()}" for c in theses]
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
    if sources:
        lines += ["", "## Sources"]
        lines += [
            f"- [{c['title']}](/{c['path']}) — {c['evidenceLabel'] or 'source'}"
            for c in sources
        ]
    if not theses and not sources:
        lines += ["", "_No durable knowledge has been promoted yet._"]
    return {
        "path": "index.md",
        "conceptId": "index",
        "kind": "index",
        "type": "Index",
        "title": project.question or project.title or "Research project",
        "description": "",
        "frontmatter": {},
        "yaml": "",
        "body": "\n".join(lines),
        "links": [],
        "evidenceLabel": "",
        "confidence": None,
        "log": None,
    }


def _log_concept(concepts):
    by_date = {}
    for c in concepts:
        if c.get("log"):
            date, line = c["log"]
            by_date.setdefault(date, []).append(line)
    lines = ["# Log"]
    for date in sorted(by_date, reverse=True):
        lines += ["", f"## {date}", *by_date[date]]
    return {
        "path": "log.md",
        "conceptId": "log",
        "kind": "log",
        "type": "Log",
        "title": "Log",
        "description": "Promotion history, newest first.",
        "frontmatter": {},
        "yaml": "",
        "body": "\n".join(lines),
        "links": [],
        "evidenceLabel": "",
        "confidence": None,
        "log": None,
    }


def _collect(project):
    """Query the durable layer once and build ordered concept dicts:
    [index, *theses, *sources, log]."""
    memories = list(
<<<<<<< HEAD
        ResearchProjectMemory.active_objects.filter(project=project).order_by(
            "created_at"
        )
=======
        ResearchProjectMemory.active_objects.filter(project=project).order_by("created_at")
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
    )
    kis = list(
        ResearchKnowledgeItem.active_objects.filter(project=project)
        .select_related("source_staging_item")
        .order_by("created_at")
    )
    cites = _citation_links(project, kis)
    ts_links = ResearchThesisSource.active_objects.filter(
        thesis__project=project
    ).select_related("thesis", "source", "source__source_staging_item")
    sources_by_thesis, theses_by_source = {}, {}
    for link in ts_links:
        si = link.source.source_staging_item
        stance = link.stance or (si.evidence_label if si else "") or ""
        sources_by_thesis.setdefault(link.thesis_id, []).append((link.source, stance))
        theses_by_source.setdefault(link.source_id, []).append((link.thesis, stance))
    theses = [
<<<<<<< HEAD
        _thesis_concept(m, project.field, sources_by_thesis.get(m.id, []))
        for m in memories
    ]
    sources = [
        _source_concept(ki, cites.get(ki.id), theses_by_source.get(ki.id, []))
        for ki in kis
=======
        _thesis_concept(m, project.field, sources_by_thesis.get(m.id, [])) for m in memories
    ]
    sources = [
        _source_concept(ki, cites.get(ki.id), theses_by_source.get(ki.id, [])) for ki in kis
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
    ]
    index_c = _index_concept(project, theses, sources)
    log_c = _log_concept(theses + sources)
    return [index_c, *theses, *sources, log_c]


def _link_graph(concepts):
    """Nodes for theses + sources; edges from observed source -> source cites."""
    knowledge = [c for c in concepts if c["kind"] in ("thesis", "source")]
    ids = {c["conceptId"] for c in knowledge}
    nodes = [
        {
            "id": c["conceptId"],
            "kind": c["kind"],
            "type": c["type"],
            "label": c["title"],
            "evidenceLabel": c["evidenceLabel"],
            "confidence": c["confidence"],
        }
        for c in knowledge
    ]
    edges = [
<<<<<<< HEAD
        {
            "source": c["conceptId"],
            "target": link["to"],
            "kind": link.get("kind", "cites"),
        }
=======
        {"source": c["conceptId"], "target": link["to"], "kind": link.get("kind", "cites")}
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
        for c in knowledge
        for link in c["links"]
        if link["to"] in ids
    ]
    return {"nodes": nodes, "edges": edges}


def build_okf_bundle(project):
    """The project's durable knowledge as an OKF v0.1 bundle:
    {bundle_relative_path: file_contents}."""
    bundle = {}
    for c in _collect(project):
<<<<<<< HEAD
        md = (
            (c["yaml"] + "\n\n" + c["body"]).strip() if c["yaml"] else c["body"].strip()
        )
=======
        md = (c["yaml"] + "\n\n" + c["body"]).strip() if c["yaml"] else c["body"].strip()
>>>>>>> ae7fe4f (feat(research): OKF export + thesis-source linking (backend))
        bundle[c["path"]] = md + "\n"
    return bundle


def build_okf_view(project):
    """Structured JSON for the in-app Maps viewer: ordered files (with parsed
    frontmatter + markdown body) and the source-citation link graph."""
    concepts = _collect(project)
    files = [
        {
            "path": c["path"],
            "conceptId": c["conceptId"],
            "kind": c["kind"],
            "type": c["type"],
            "title": c["title"],
            "description": c["description"],
            "frontmatter": c["frontmatter"],
            "body": c["body"],
            "links": c["links"],
            "evidenceLabel": c["evidenceLabel"],
            "confidence": c["confidence"],
        }
        for c in concepts
    ]
    return {"files": files, "graph": _link_graph(concepts)}


def bundle_filename(project):
    base = _slug(project.title or project.question, f"project-{project.id}")
    return f"{base}-{project.id}-okf.zip"
