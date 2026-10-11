"""
Seed two lived-in research projects that show the chat hub and learning loop.

    python manage.py seed_research_demo --email someone@example.com [--reset]

Scenario A is a public-health PhD writing a chapter on AI triage in emergency
departments, nine days in: three Scout sessions, review notes, approved memory,
a learned procedure, and corrections falling run by run. Scenario B is the same
scholar starting an education policy brief two days ago, where the standards
learned in A already apply on day one and the ED scope does not.

Findings that get approved are real, published papers; links are PubMed or
Google Scholar searches by title rather than guessed DOIs. Findings that get
rejected as "a preprint" or "a vendor white paper" are named as the kind of
item they are, not as invented citations. Nothing here quotes a paper.
"""

import shutil
from datetime import timedelta
from urllib.parse import quote_plus

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from research.constants import (
    AgentRunStatus,
    ChatMessageRole,
    MemoryLayer,
    MemoryProposalOrigin,
    MemoryProposalStatus,
    ProjectMemorySource,
    ResearchSessionMode,
    SoulFileOrigin,
    StagingItemStatus,
    ThreadEventType,
)
from research.models import (
    ResearchAgentRun,
    ResearchChatMessage,
    ResearcherProfile,
    ResearchKnowledgeItem,
    ResearchMemoryProposal,
    ResearchProcedure,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    ResearchStagingItem,
    SoulFile,
    SoulFileVersion,
)
from research.services.hermes_service import get_hermes_service
from research.services.learning_service import (
    SKILL_CATEGORY,
    render_learning_everywhere,
)
from research.services.profile_service import profile_home_for

DEMO_FIELD_SUFFIX = " · demo"
DEMO_PROCEDURE = "verify-before-staging"

STANDARDS_AT_SIGNUP = [
    "Before staging, confirm each DOI or PMID resolves to the paper you describe; "
    "drop anything you cannot verify.",
    "Always report sample size, design and effect size (with confidence intervals "
    "when given).",
    "Format citations in APA 7.",
]

VERIFY_SKILL = """---
name: verify-before-staging
description: Check a source the way this scholar wants before it reaches the inbox
---
Follow these steps for every candidate, in order, before staging it.

1. Resolve the identifier. Look the DOI or PMID up and confirm title, first author
   and year match. If they do not, drop the candidate and say so.
2. Check retractions. Search Retraction Watch and the publisher page for a
   retraction, correction or expression of concern. Report what you found.
3. Check funding and authorship. Read the funding and competing-interest
   statements; if a vendor funded the work or employs an author, say so in the
   rationale.
4. Look for replication. Search for an independent external validation or
   replication. A single-site or developer-run study is "promising", not
   "established", until one exists.
5. Report the design and numbers: setting, n, design, primary outcome, effect
   size with its interval.
"""


ED_SOUL = """# Identity

You are a rigorous research collaborator for a public-health PhD student
writing a dissertation chapter on clinical AI. You care more about what the
evidence shows than about sounding confident, and you treat every claim about
patient benefit as something that has to be defended.

## Style

- Lead with the judgement, then the evidence behind it
- Report setting, n, design and effect size with its interval for every study
- Distinguish discrimination (AUROC) from patient outcomes; never let one stand in for the other
- Be blunt about weak designs; a retrospective single-site model is promising, not established

## Evidence standards

- Before staging, confirm each DOI or PMID resolves to the paper you describe; drop anything you cannot verify.
- Always report sample size, design and effect size (with confidence intervals when given).
- Format citations in APA 7.
- Stage only peer-reviewed work. Set preprints aside and say how many, so I can ask to see them.
- Flag industry funding, vendor authorship or white papers as a conflict of interest in the rationale.

## Avoid

- Vendor performance claims without independent validation
- Treating a model's AUROC as evidence that patients did better
- Citing anything you have not actually read

## Defaults

- When an external validation contradicts the developer's numbers, lead with the external validation
- When unsure whether a study is in scope, stage it with a note rather than drop it silently
"""

MINDSET_SOUL = """# Identity

You are a patient research explainer helping an education researcher brief a
school board. You turn contested evidence into something non-specialist
decision-makers can act on, without losing accuracy.

## Style

- Lead with what the evidence means for the funding decision
- Translate effect sizes into practical terms (GPA points, percentiles)
- Name who ran each trial and whether anyone independent replicated it
- Keep caveats short and specific

## Evidence standards

- Before staging, confirm each DOI or PMID resolves to the paper you describe; drop anything you cannot verify.
- Always report sample size, design and effect size (with confidence intervals when given).
- Format citations in APA 7.
- Stage only peer-reviewed work. Set preprints aside and say how many, so I can ask to see them.
- Flag industry funding, vendor authorship or white papers as a conflict of interest in the rationale.

## Avoid

- Popular books and vendor case studies as evidence
- Implying an average effect applies to every student
- Overstating certainty to make a point land

## Defaults

- When meta-analyses disagree, show both and explain what drives the difference
- End every summary with what it means for the district's decision
"""


def scholar(title):
    return f"https://scholar.google.com/scholar?q={quote_plus(title)}"


def pubmed(title):
    return f"https://pubmed.ncbi.nlm.nih.gov/?term={quote_plus(title)}"


# Each finding: title, authors, year, venue, url, evidence label, confidence,
# rationale, decision (approve/reject/later/None=pending), note, tags.
ED = {
    "title": "AI triage in emergency departments",
    "field": "Public health · PhD chapter 3",
    "question": (
        "Do machine-learning triage and early-warning tools improve outcomes or "
        "time-to-treatment for adult emergency department patients, compared "
        "with standard triage (e.g. ESI)?"
    ),
    "template": "empirical-rigor",
    "soul": ED_SOUL,
    "scope": [
        "Population or setting: Adult patients in emergency departments",
        "Include: Triage accuracy, under/over-triage, time to treatment, "
        "mortality, ICU admission",
        "Study designs: RCTs, prospective or external-validation cohorts; "
        "reviews only for framing",
    ],
    "days_ago": 9,
    "sessions": [
        {
            "days_ago": 9,
            "thread": "Mapping the evidence for chapter 3",
            "focus": "Outcomes vs ESI",
            "task": (
                "Find studies comparing ML-based ED triage against the Emergency "
                "Severity Index on clinical outcomes (critical care, admission, "
                "mortality). Adults. Report n, setting and effect sizes."
            ),
            "learning": {},
            "findings": [
                (
                    "Machine-Learning-Based Electronic Triage More Accurately "
                    "Differentiates Patients With Respect to Clinical Outcomes "
                    "Compared With the Emergency Severity Index",
                    "Levin S; Toerper M; Hamrock E; et al.",
                    2018,
                    "Annals of Emergency Medicine",
                    "pubmed",
                    "supporting",
                    0.85,
                    "Multi-site retrospective comparison of an e-triage model "
                    "against ESI across adult ED visits; directly on the chapter "
                    "question.",
                    "approve",
                    "Exactly the comparison I need. Large n, three EDs.",
                    ["strong_evidence"],
                ),
                (
                    "Emergency department triage prediction of clinical outcomes "
                    "using machine learning models",
                    "Raita Y; Goto T; Faridi MK; et al.",
                    2019,
                    "Critical Care",
                    "pubmed",
                    "supporting",
                    0.8,
                    "National sample (NHAMCS) comparing ML models with "
                    "conventional triage for critical-care outcome and admission.",
                    "approve",
                    "",
                    [],
                ),
                (
                    "Predicting hospital admission at emergency department triage "
                    "using machine learning",
                    "Hong WS; Haimovich AD; Taylor RA",
                    2018,
                    "PLOS ONE",
                    "pubmed",
                    "partial",
                    0.7,
                    "Admission prediction at triage; outcome is disposition rather "
                    "than patient harm, so partial support.",
                    "approve",
                    "Keep, but it's disposition not outcomes — say that in the chapter.",
                    [],
                ),
                (
                    "Automated Triaging of Adult Chest Radiographs with Deep "
                    "Artificial Neural Networks",
                    "Annarumma M; Withey SJ; Bakewell RJ; et al.",
                    2019,
                    "Radiology",
                    "pubmed",
                    "tangential",
                    0.5,
                    "Triage of radiograph reading queues using deep learning.",
                    "reject",
                    "This is radiology reporting triage, not ED patient triage.",
                    ["off_scope"],
                ),
                (
                    "Machine Learning-Based Prediction of Clinical Outcomes for "
                    "Children During Emergency Department Triage",
                    "Goto T; Camargo CA Jr; Faridi MK; et al.",
                    2019,
                    "JAMA Network Open",
                    "pubmed",
                    "partial",
                    0.6,
                    "Same question in a paediatric population.",
                    "reject",
                    "Paediatric only — this chapter is adults.",
                    ["off_scope"],
                ),
                (
                    "Vendor white paper: AI triage platform cuts door-to-doctor "
                    "time (company report)",
                    "Vendor marketing team",
                    2023,
                    "Company website",
                    "",
                    "supporting",
                    0.4,
                    "Reports large time savings after deployment at customer sites.",
                    "reject",
                    "Vendor white paper — conflict of interest, and no methods.",
                    ["conflict_of_interest"],
                ),
                (
                    "medRxiv preprint: transformer model for ED acuity from "
                    "free-text triage notes",
                    "Preprint authors",
                    2024,
                    "medRxiv (not peer reviewed)",
                    "",
                    "supporting",
                    0.5,
                    "Claims higher AUROC than ESI on one hospital's notes.",
                    "reject",
                    "Preprints aren't enough for this chapter.",
                    ["preprint"],
                ),
                (
                    "High-performance medicine: the convergence of human and "
                    "artificial intelligence",
                    "Topol EJ",
                    2019,
                    "Nature Medicine",
                    "pubmed",
                    "tangential",
                    0.4,
                    "Broad narrative review of clinical AI.",
                    "later",
                    "Useful framing for the intro, not evidence.",
                    [],
                ),
            ],
            "proposals": [
                (
                    MemoryLayer.RESEARCHER,
                    "Stage only peer-reviewed work. Set preprints aside and say "
                    "how many, so I can ask to see them.",
                    "Preprints aren't enough for this chapter.",
                    MemoryProposalOrigin.FOREGROUND,
                ),
                (
                    MemoryLayer.RESEARCHER,
                    "Flag industry funding, vendor authorship or white papers as "
                    "a conflict of interest in the rationale.",
                    "Vendor white paper — conflict of interest, and no methods.",
                    MemoryProposalOrigin.FOREGROUND,
                ),
                (
                    MemoryLayer.PROJECT,
                    "Exclude: radiology reporting triage and paediatric-only "
                    "studies — this chapter is adult ED patient triage.",
                    "This is radiology reporting triage, not ED patient triage.",
                    MemoryProposalOrigin.BACKGROUND_REVIEW,
                ),
            ],
        },
        {
            "days_ago": 6,
            "thread": "Mapping the evidence for chapter 3",
            "focus": "Sepsis early warning",
            "task": (
                "Find prospective or external-validation evidence on ML sepsis "
                "early-warning tools used in or from the ED, with patient "
                "outcomes or time-to-antibiotics."
            ),
            "learning": {
                "applied": [
                    {
                        "memory": "Stage only peer-reviewed work; set preprints aside",
                        "effect": "1 preprint set aside",
                    },
                    {
                        "memory": "Flag industry funding as a conflict of interest",
                        "effect": "TREWS and InSight studies flagged for developer/vendor ties",
                    },
                ],
                "setAside": [
                    {
                        "title": "medRxiv preprint: LLM sepsis screening from "
                        "triage vitals",
                        "url": "",
                        "reason": "Preprint — your standing rule",
                    }
                ],
            },
            "findings": [
                (
                    "External Validation of a Widely Implemented Proprietary "
                    "Sepsis Prediction Model in Hospitalized Patients",
                    "Wong A; Otles E; Donnelly JP; et al.",
                    2021,
                    "JAMA Internal Medicine",
                    "pubmed",
                    "disputing",
                    0.85,
                    "Independent external validation of the Epic Sepsis Model "
                    "found much weaker discrimination than the vendor reported.",
                    "approve",
                    "Independent validation — exactly the scrutiny I want.",
                    ["strong_evidence"],
                ),
                (
                    "Prospective, multi-site study of patient outcomes after "
                    "implementation of the TREWS machine learning-based early "
                    "warning system for sepsis",
                    "Adams R; Henry KE; Sridharan A; et al.",
                    2022,
                    "Nature Medicine",
                    "pubmed",
                    "supporting",
                    0.75,
                    "Prospective five-hospital study linking prompt alert "
                    "confirmation with lower mortality. Flagged: developers "
                    "report equity in the commercialising company.",
                    "approve",
                    "Keep, and keep the COI flag in the chapter.",
                    [],
                ),
                (
                    "Effect of a machine learning-based severe sepsis prediction "
                    "algorithm on patient survival and hospital length of stay: a "
                    "randomised clinical trial",
                    "Shimabukuro DW; Barton CW; Feldman MD; et al.",
                    2017,
                    "BMJ Open Respiratory Research",
                    "pubmed",
                    "supporting",
                    0.55,
                    "Small single-centre RCT (two ICUs) of a vendor algorithm. "
                    "Flagged: vendor-funded.",
                    "reject",
                    "n=142, two ICUs, vendor-funded — too weak to lean on, and ICU not ED.",
                    ["weak_method", "conflict_of_interest"],
                ),
                (
                    "Real-World Integration of a Sepsis Deep Learning Technology "
                    "Into Routine Clinical Care: Implementation Study",
                    "Sendak MP; Ratliff W; Sarro D; et al.",
                    2020,
                    "JMIR Medical Informatics",
                    "pubmed",
                    "partial",
                    0.65,
                    "How Duke deployed Sepsis Watch in the ED: workflow, roles, "
                    "governance. Implementation evidence rather than outcomes.",
                    "approve",
                    "",
                    [],
                ),
            ],
            "proposals": [
                (
                    MemoryLayer.PROCEDURE,
                    "verify-before-staging",
                    "n=142, two ICUs, vendor-funded — too weak to lean on.",
                    MemoryProposalOrigin.BACKGROUND_REVIEW,
                ),
            ],
        },
        {
            "days_ago": 3,
            "thread": "Mapping the evidence for chapter 3",
            "focus": "Adoption & workflow",
            "task": (
                "What determines whether ED clinicians act on ML triage or "
                "sepsis alerts? Adoption, alert fatigue, timing effects."
            ),
            "learning": {
                "applied": [
                    {
                        "memory": "verify-before-staging (your skill)",
                        "effect": "All 4 identifiers resolved; no retractions found; 1 developer COI flagged",
                    },
                    {
                        "memory": "Exclude radiology and paediatric-only studies",
                        "effect": "2 radiology-queue papers left out",
                    },
                    {
                        "memory": "Stage only peer-reviewed work",
                        "effect": "1 preprint set aside",
                    },
                ],
                "setAside": [
                    {
                        "title": "arXiv preprint: reinforcement-learning triage "
                        "policy in a simulated ED",
                        "url": "",
                        "reason": "Preprint, and simulation only",
                    }
                ],
            },
            "findings": [
                (
                    "Factors driving provider adoption of the TREWS machine "
                    "learning-based early warning system and its effects on "
                    "sepsis treatment timing",
                    "Henry KE; Adams R; Parent C; et al.",
                    2022,
                    "Nature Medicine",
                    "pubmed",
                    "supporting",
                    0.75,
                    "Prospective adoption study: which clinicians acted on alerts "
                    "and how that changed time to antibiotics. Developer COI "
                    "flagged, as for Adams 2022.",
                    "approve",
                    "",
                    [],
                ),
                (
                    "Clinical decision support systems for triage in the "
                    "emergency department using intelligent systems: a review",
                    "Fernandes M; Vieira SM; Leite F; et al.",
                    2020,
                    "Artificial Intelligence in Medicine",
                    "pubmed",
                    "partial",
                    0.65,
                    "Review of ED triage decision-support systems; good map of "
                    "the field and its validation gaps.",
                    "approve",
                    "",
                    [],
                ),
                (
                    "Machine learning methods applied to triage in emergency "
                    "services: A systematic review",
                    "Sánchez-Salmerón R; Gómez-Urquiza JL; Albendín-García L; et al.",
                    2022,
                    "International Emergency Nursing",
                    "pubmed",
                    "partial",
                    0.6,
                    "Systematic review; most included studies retrospective "
                    "single-centre — supports the chapter's 'promising, not "
                    "established' framing.",
                    "approve",
                    "",
                    [],
                ),
                (
                    "Artificial intelligence algorithm to predict the need for "
                    "critical care in prehospital emergency medical services",
                    "Kang DY; Cho KJ; Kwon O; et al.",
                    2020,
                    "Scandinavian Journal of Trauma, Resuscitation and "
                    "Emergency Medicine",
                    "pubmed",
                    "tangential",
                    0.5,
                    "Prehospital EMS prediction.",
                    "reject",
                    "Prehospital EMS, not the ED.",
                    ["off_scope"],
                ),
            ],
            "proposals": [],
        },
    ],
    "live": {
        "thread": "Chapter 3 — what works in practice",
        "focus": "Prospective deployments",
        "task": (
            "Prospective or stepped-wedge evaluations of ML e-triage deployed "
            "in adult EDs, with patient-distribution or outcome measures."
        ),
        "learning": {
            "applied": [
                {
                    "memory": "verify-before-staging (your skill)",
                    "effect": "Both identifiers resolved; no retractions",
                },
                {
                    "memory": "Stage only peer-reviewed work",
                    "effect": "2 preprints set aside",
                },
            ],
            "setAside": [
                {
                    "title": "medRxiv preprint: LLM-assisted ESI assignment in "
                    "a single urban ED",
                    "url": "",
                    "reason": "Preprint — your standing rule",
                },
                {
                    "title": "Research Square preprint: e-triage and left-without-"
                    "being-seen rates",
                    "url": "",
                    "reason": "Preprint — your standing rule",
                },
            ],
        },
        "findings": [
            (
                "An Electronic Emergency Triage System to Improve Patient "
                "Distribution by Critical Outcomes",
                "Dugas AF; Kirsch TD; Toerper M; et al.",
                2016,
                "Journal of Emergency Medicine",
                "pubmed",
                "supporting",
                0.75,
                "E-triage evaluated against ESI on how well it separates "
                "patients by critical outcomes; same group as Levin 2018.",
            ),
            (
                "Validation of deep-learning-based triage and acuity score using "
                "a large national dataset",
                "Kwon JM; Lee Y; Lee Y; et al.",
                2018,
                "PLOS ONE",
                "pubmed",
                "supporting",
                0.7,
                "Korean national ED dataset; deep-learning acuity score "
                "outperforms the Korean triage scale on critical-care and "
                "mortality outcomes. Retrospective — not prospective.",
            ),
        ],
    },
}

MINDSET = {
    "title": "Growth-mindset interventions: brief for the district",
    "field": "Education policy · brief for a school district",
    "question": (
        "Should the district fund a growth-mindset programme for its secondary "
        "schools — do these interventions raise grades, for whom, and by how "
        "much?"
    ),
    "template": "empirical-rigor",
    "soul": MINDSET_SOUL,
    "scope": [
        "Population or setting: Secondary (grades 6–12) students in "
        "high-income school systems",
        "Include: Grades or GPA as the outcome; self-reported mindset alone "
        "does not count",
        "Time period: 2015 onward unless foundational",
    ],
    "days_ago": 2,
    "sessions": [
        {
            "days_ago": 2,
            "thread": "Does this work at scale?",
            "carried_note": (
                "New project, but I already know how you work: from your ED "
                "project I'm applying *peer-reviewed only*, *flag conflicts of "
                "interest* and your *verify-before-staging* procedure. I did "
                "**not** carry over the ED project's scope — this brief is about "
                "secondary students, so I'll use the scope you set here."
            ),
            "focus": "Large trials & meta-analyses",
            "task": (
                "Find the largest preregistered trials and meta-analyses of "
                "growth-mindset interventions on secondary-school grades."
            ),
            "learning": {
                "applied": [
                    {
                        "memory": "Stage only peer-reviewed work (learned in your ED project)",
                        "effect": "1 preprint set aside",
                    },
                    {
                        "memory": "Flag industry funding as a conflict of interest (learned in your ED project)",
                        "effect": "1 vendor case study flagged",
                    },
                    {
                        "memory": "verify-before-staging (your skill)",
                        "effect": "All identifiers resolved; Li & Bates surfaced as a failed replication",
                    },
                ],
                "setAside": [
                    {
                        "title": "PsyArXiv preprint: mindset micro-intervention "
                        "in one charter school",
                        "url": "",
                        "reason": "Preprint — your standing rule",
                    }
                ],
            },
            "findings": [
                (
                    "A national experiment reveals where a growth mindset "
                    "improves achievement",
                    "Yeager DS; Hanselman P; Walton GM; et al.",
                    2019,
                    "Nature",
                    "scholar",
                    "supporting",
                    0.85,
                    "Preregistered nationally representative US trial (National "
                    "Study of Learning Mindsets); small average GPA effect, "
                    "concentrated among lower-achieving students.",
                    "approve",
                    "Preregistered, national — but stress how small the average effect is.",
                    ["strong_evidence"],
                ),
                (
                    "To What Extent and Under Which Circumstances Are Growth "
                    "Mind-Sets Important to Academic Achievement? Two "
                    "Meta-Analyses",
                    "Sisk VF; Burgoyne AP; Sun J; et al.",
                    2018,
                    "Psychological Science",
                    "scholar",
                    "disputing",
                    0.8,
                    "Two meta-analyses finding weak links between mindset and "
                    "achievement and weak average intervention effects.",
                    "approve",
                    "The counter-evidence the board needs to see.",
                    [],
                ),
                (
                    "Mind-Set Interventions Are a Scalable Treatment for Academic "
                    "Underachievement",
                    "Paunesku D; Walton GM; Romero C; et al.",
                    2015,
                    "Psychological Science",
                    "scholar",
                    "supporting",
                    0.65,
                    "Online intervention across 13 high schools; effects among "
                    "at-risk students.",
                    "approve",
                    "",
                    [],
                ),
                (
                    "You can't change your basic ability, but you work at things, "
                    "and that's how we get hard things done: Testing the role of "
                    "growth mindset on response to setbacks, educational "
                    "attainment, and cognitive ability",
                    "Li Y; Bates TC",
                    2019,
                    "Journal of Experimental Psychology: General",
                    "scholar",
                    "disputing",
                    0.7,
                    "Preregistered studies that did not reproduce the predicted "
                    "mindset effects.",
                    "approve",
                    "Failed replication — include it.",
                    [],
                ),
                (
                    "Implicit Theories of Intelligence Predict Achievement Across "
                    "an Adolescent Transition: A Longitudinal Study and an "
                    "Intervention",
                    "Blackwell LS; Trzesniewski KH; Dweck CS",
                    2007,
                    "Child Development",
                    "scholar",
                    "supporting",
                    0.55,
                    "Foundational study: correlational longitudinal sample plus "
                    "a small intervention.",
                    "reject",
                    "Foundational, but n≈91 in the intervention — can't carry a district-scale claim.",
                    ["weak_method"],
                ),
                (
                    "Vendor case study: mindset curriculum lifts GPA across a "
                    "partner district (company report)",
                    "Curriculum vendor",
                    2022,
                    "Company website",
                    "",
                    "supporting",
                    0.35,
                    "Before/after GPA comparison with no control group. "
                    "Flagged: produced by the vendor selling the programme.",
                    "reject",
                    "Vendor case study, no control group.",
                    ["conflict_of_interest", "weak_method"],
                ),
                (
                    "Mindset: The New Psychology of Success",
                    "Dweck CS",
                    2006,
                    "Random House (book)",
                    "scholar",
                    "tangential",
                    0.3,
                    "Popular book that introduced the idea.",
                    "reject",
                    "Popular book, not evidence.",
                    ["off_scope"],
                ),
            ],
            "proposals": [],
        },
    ],
    "live": {
        "thread": "Effect sizes for the board",
        "focus": "Recent meta-analyses & replications",
        "task": (
            "Find meta-analyses and replications since 2019 that estimate the "
            "average effect of growth-mindset interventions on grades, and who "
            "benefits."
        ),
        "learning": {
            "applied": [
                {
                    "memory": "verify-before-staging (your skill)",
                    "effect": "All 5 identifiers resolved; noted the two 2023 meta-analyses disagree",
                },
                {
                    "memory": "Population: secondary students in high-income systems",
                    "effect": "1 university-only study left out",
                },
            ],
            "setAside": [],
        },
        "findings": [
            (
                "Do growth mindset interventions impact students' academic "
                "achievement? A systematic review and meta-analysis with "
                "recommendations for best practices",
                "Macnamara BN; Burgoyne AP",
                2023,
                "Psychological Bulletin",
                "scholar",
                "disputing",
                0.8,
                "Meta-analysis finding little to no average effect once study "
                "quality and potential bias are accounted for.",
            ),
            (
                "A systematic review and meta-analysis of growth mindset "
                "interventions: For whom, how, and why might such interventions "
                "work?",
                "Burnette JL; Billingsley J; Banks GC; et al.",
                2023,
                "Psychological Bulletin",
                "scholar",
                "partial",
                0.8,
                "Meta-analysis finding small effects concentrated in specific "
                "subgroups and implementations — read against Macnamara & "
                "Burgoyne 2023.",
            ),
            (
                "Changing Mindsets: Effectiveness trial",
                "Foliano F; Rolfe H; Buzzeo J; et al.",
                2019,
                "Education Endowment Foundation evaluation report",
                "scholar",
                "disputing",
                0.7,
                "Independent UK school-based trial commissioned by the EEF; no "
                "detectable effect on attainment.",
            ),
            (
                "Teacher mindsets help explain where a growth-mindset "
                "intervention does and doesn't work",
                "Yeager DS; Carroll JM; Buontempo J; et al.",
                2022,
                "Psychological Science",
                "scholar",
                "partial",
                0.7,
                "Secondary analysis of the national trial: effects appear where "
                "teachers hold growth mindsets — relevant to implementation.",
            ),
            (
                "How can we inspire nations of learners? An investigation of "
                "growth mindset and challenge-seeking in two countries",
                "Rege M; Hanselman P; Solli IF; et al.",
                2021,
                "American Psychologist",
                "scholar",
                "supporting",
                0.65,
                "Replication of the US intervention in Norway with effects on "
                "challenge-seeking.",
            ),
        ],
    },
    "pending_proposals": [
        (
            MemoryLayer.RESEARCHER,
            "Treat author-run trials as promising until an independent trial "
            "replicates them; say who ran each trial.",
            "Failed replication — include it.",
            MemoryProposalOrigin.BACKGROUND_REVIEW,
        ),
        (
            MemoryLayer.PROJECT,
            "Report effects in SD units and translate them into GPA points, so "
            "the board can judge whether they are practically meaningful.",
            "Preregistered, national — but stress how small the average effect is.",
            MemoryProposalOrigin.FOREGROUND,
        ),
    ],
}


class Command(BaseCommand):
    help = "Seed two lived-in research-mode demo projects for one scholar."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete this scholar's earlier demo projects first.",
        )

    def handle(self, *args, email, reset, **options):
        user = get_user_model().objects.filter(email=email).first()
        if user is None:
            raise CommandError(f"No user with email {email}.")
        if reset:
            ResearchProcedure.objects.filter(
                user=user, project__isnull=True, name=DEMO_PROCEDURE
            ).delete()
            ResearchProject.objects.filter(
                user=user, field__endswith=DEMO_FIELD_SUFFIX
            ).delete()
        self.now = timezone.now()
        with transaction.atomic():
            ResearcherProfile.objects.update_or_create(
                user=user, defaults={"content": "\n§\n".join(STANDARDS_AT_SIGNUP)}
            )
            ed = self._project(user, ED)
            mindset = self._project(user, MINDSET)
        for project, spec in ((ed, ED), (mindset, MINDSET)):
            self._clear_stale_profile(project)
            get_hermes_service(project).provision_soul(spec["soul"].strip())
        render_learning_everywhere(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded projects {ed.id} (ED triage) and {mindset.id} "
                f"(growth mindset) for {email}."
            )
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _clear_stale_profile(self, project):
        """A profile directory can outlive its database (ids get reused after a
        reset). These projects are brand new, so anything already there is
        someone else's memory — remove it before DARE renders theirs."""
        home = profile_home_for(project)
        for name in ("memories", "pending", "sessions"):
            shutil.rmtree(home / name, ignore_errors=True)
        shutil.rmtree(home / "skills" / SKILL_CATEGORY, ignore_errors=True)

    def _at(self, days_ago, minutes=0):
        return self.now - timedelta(days=days_ago) + timedelta(minutes=minutes)

    def _stamp(self, obj, when):
        type(obj).objects.filter(pk=obj.pk).update(created_at=when)

    def _project(self, user, spec):
        start = self._at(spec["days_ago"], -60)
        project = ResearchProject.objects.create(
            user=user,
            title=spec["title"],
            field=spec["field"] + DEMO_FIELD_SUFFIX,
            question=spec["question"],
            standards_template=spec["template"],
            enabled_tools=["web"],
        )
        self._stamp(project, start)
        soul = SoulFile.objects.create(project=project)
        SoulFileVersion.objects.create(
            soul_file=soul,
            version=1,
            content=spec["soul"].strip(),
            origin=SoulFileOrigin.EDIT,
            created_by=user,
        )
        for rule in spec["scope"]:
            memory = ResearchProjectMemory.objects.create(
                project=project,
                added_by=user,
                label=rule.split(":", 1)[0],
                detail=rule,
                source=ProjectMemorySource.MANUAL,
            )
            self._stamp(memory, start)

        threads = {}
        for index, session in enumerate(spec["sessions"]):
            thread = threads.get(session["thread"])
            if thread is None:
                thread = self._thread(project, user, session, first=index == 0)
                threads[session["thread"]] = thread
            self._session(project, user, thread, session, index)
        live = self._live(project, user, spec["live"])
        for proposal in spec.get("pending_proposals", []):
            self._proposal(
                project, user, live, proposal, decided=False, when=self._at(0, -15)
            )
        return project

    def _thread(self, project, user, session, first):
        thread = ResearchSession.create_thread(project, user, session["thread"])
        when = self._at(session["days_ago"], -50)
        self._stamp(thread, when)
        opening = (
            f"I'm starting the evidence review for this project. {project.question} "
            "Can you plan a first pass?"
        )
        self._message(thread, user, ChatMessageRole.USER, opening, when)
        carried = session.get("carried_note", "")
        self._message(
            thread,
            user,
            ChatMessageRole.ASSISTANT,
            (carried + "\n\n" if carried else "")
            + "Here's a first pass. I'll keep to your standing rules — verified "
            "identifiers, sample and effect sizes, APA 7.\n\n"
            f"1. **{session['focus']}** — {session['task']}\n\n"
            "I'll start with that one Scout so you can tell me what you think of "
            "its picks before we widen out. Launch it?",
            when + timedelta(minutes=1),
        )
        self._message(
            thread, user, ChatMessageRole.USER, "Yes, go.", when + timedelta(minutes=3)
        )
        return thread

    def _message(self, thread, user, role, content, when, event_type="", payload=None):
        message = ResearchChatMessage.objects.create(
            session=thread,
            project=thread.project,
            user=user,
            role=role,
            content=content,
            event_type=event_type,
            payload=payload or {},
        )
        self._stamp(message, when)
        return message

    def _run(self, project, user, thread, spec, days_ago, status):
        scout_session = ResearchSession._get_or_create(
            project, user, ResearchSessionMode.SCOUT
        )
        started = self._at(days_ago, -40)
        run = ResearchAgentRun.objects.create(
            session=scout_session,
            thread=thread,
            project=project,
            user=user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            task=spec["task"],
            status=status,
            status_detail=f"Staged {len(spec['findings'])} findings.",
            selected_context={"focus": spec["focus"], "depth": "deep"},
            learning=spec["learning"],
            soul_file_version="v1",
            allowed_tools=["web"],
            started_at=started,
            completed_at=started + timedelta(minutes=6),
        )
        self._stamp(run, started)
        self._message(
            thread,
            user,
            ChatMessageRole.EVENT,
            spec["focus"],
            started,
            ThreadEventType.SCOUTS_LAUNCHED,
            {
                "scouts": [
                    {"runId": run.id, "focus": spec["focus"], "task": spec["task"]}
                ]
            },
        )
        return run

    def _stage(self, project, run, finding, when):
        title, authors, year, venue, link, label, confidence, rationale = finding[:8]
        url = pubmed(title) if link == "pubmed" else scholar(title) if link else ""
        item = ResearchStagingItem.objects.create(
            project=project,
            run=run,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            url=url,
            rationale=rationale,
            confidence=confidence,
            evidence_label=label,
            provenance={
                "tool": "web",
                "query": run.task,
                "retrievedAt": when.isoformat(),
                "soulFileVersion": 1,
                "role": "scout",
                "runId": run.id,
            },
        )
        self._stamp(item, when)
        return item

    def _session(self, project, user, thread, spec, index):
        run = self._run(
            project, user, thread, spec, spec["days_ago"], AgentRunStatus.COMPLETED
        )
        staged_at = self._at(spec["days_ago"], -34)
        items = [self._stage(project, run, f, staged_at) for f in spec["findings"]]
        self._message(
            thread,
            user,
            ChatMessageRole.EVENT,
            f"{spec['focus']}: Staged {len(items)} findings.",
            staged_at,
            ThreadEventType.SCOUT_FINISHED,
            {"runId": run.id, "focus": spec["focus"], "status": "completed"},
        )
        set_aside = len(spec["learning"].get("setAside") or [])
        self._message(
            thread,
            user,
            ChatMessageRole.ASSISTANT,
            f"**{spec['focus']}** staged {len(items)} findings to your inbox"
            + (
                f", and I set aside {set_aside} because of your standing rules — "
                "say if you want to see them"
                if set_aside
                else ""
            )
            + ". Strongest first: "
            + items[0].title
            + ".",
            staged_at + timedelta(minutes=1),
        )
        reviewed_at = self._at(spec["days_ago"], -20)
        notes = []
        for item, finding in zip(items, spec["findings"]):
            decision, note, tags = finding[8:]
            self._decide(project, user, thread, item, decision, note, tags, reviewed_at)
            if note or tags:
                notes.append((item, decision, note, tags))
        if notes:
            reply = "Thanks — noted. " + " ".join(
                f"#{item.id}: {note or ', '.join(tags)}."
                for item, _, note, tags in notes[:3]
            )
            self._message(
                thread,
                user,
                ChatMessageRole.ASSISTANT,
                reply + " I've proposed rules from these for your approval.",
                reviewed_at + timedelta(minutes=2),
            )
        for proposal in spec["proposals"]:
            self._proposal(
                project,
                user,
                thread,
                proposal,
                decided=True,
                when=reviewed_at + timedelta(minutes=3),
            )

    def _decide(self, project, user, thread, item, decision, note, tags, when):
        status = {
            "approve": StagingItemStatus.APPROVED,
            "reject": StagingItemStatus.REJECTED,
            "later": StagingItemStatus.LATER,
        }[decision]
        item.status = status
        item.rejection_reason = note if decision == "reject" else ""
        item.later_reason = note if decision == "later" else ""
        item.review_metadata = {
            "decision": decision,
            "note": note,
            "tags": tags,
            "decidedAt": when.isoformat(),
        }
        item.save()
        if decision == "approve":
            knowledge = ResearchKnowledgeItem.objects.create(
                project=project,
                source_staging_item=item,
                approved_by=user,
                approved_at=when,
                content=item.rationale,
                rationale=item.rationale,
                provenance=item.provenance,
                soul_file_version="1",
            )
            self._stamp(knowledge, when)
        self._message(
            thread,
            user,
            ChatMessageRole.EVENT,
            item.title,
            when,
            ThreadEventType.REVIEW_DECISION,
            {
                "itemId": item.id,
                "title": item.title,
                "decision": decision,
                "note": note,
                "tags": tags,
                "runId": item.run_id,
            },
        )

    def _proposal(self, project, user, thread, spec, decided, when):
        layer, content, evidence_note, origin = spec
        payload = {"action": "add", "content": content}
        text = content
        if layer == MemoryLayer.PROCEDURE:
            payload = {"action": "create", "name": content, "content": VERIFY_SKILL}
            text = (
                "Check a source the way this scholar wants before it reaches the inbox"
            )
        proposal = ResearchMemoryProposal.objects.create(
            project=project,
            thread=thread or None,
            content=text,
            layer=layer,
            origin=origin,
            payload=payload,
            proposed_by_role="agent",
            why=f"Proposed after your recent review notes: “{evidence_note.rstrip('.')}”.",
            evidence=[
                {
                    "itemId": None,
                    "title": "",
                    "decision": "",
                    "note": evidence_note,
                    "tags": [],
                    "at": when.isoformat(),
                }
            ],
            status=(
                MemoryProposalStatus.ACCEPTED
                if decided
                else MemoryProposalStatus.PROPOSED
            ),
            accepted_by=user if decided else None,
            accepted_at=when + timedelta(minutes=5) if decided else None,
            decided_at=when + timedelta(minutes=5) if decided else None,
        )
        self._stamp(proposal, when)
        if thread:
            self._message(
                thread,
                user,
                ChatMessageRole.EVENT,
                text,
                when,
                ThreadEventType.MEMORY_PROPOSED,
                {
                    "proposalId": proposal.id,
                    "layer": layer,
                    "content": text,
                    "why": proposal.why,
                    "origin": origin,
                    "status": proposal.status,
                    "action": payload["action"],
                },
            )
        if not decided:
            return
        if layer == MemoryLayer.RESEARCHER:
            profile = ResearcherProfile.objects.get(user=user)
            if content not in profile.entries():
                profile.content = "\n§\n".join(profile.entries() + [content])
                profile.save(update_fields=["content", "updated_at"])
        elif layer == MemoryLayer.PROJECT:
            memory = ResearchProjectMemory.objects.create(
                project=project,
                added_by=user,
                label=content.split(":", 1)[0][:255],
                detail=content,
                source=ProjectMemorySource.PROPOSAL,
            )
            self._stamp(memory, when)
        else:
            procedure = ResearchProcedure.objects.create(
                user=user,
                name=content,
                content=VERIFY_SKILL,
                description="Check a source the way this scholar wants before it reaches the inbox",
                proposal=proposal,
            )
            self._stamp(procedure, when + timedelta(minutes=5))

    def _live(self, project, user, spec):
        thread = ResearchSession.create_thread(project, user, spec["thread"])
        when = self._at(0, -45)
        self._stamp(thread, when)
        ResearchSession.objects.filter(pk=thread.pk).update(
            last_run_at=self._at(0, -20)
        )
        self._message(
            thread,
            user,
            ChatMessageRole.USER,
            f"Next: {spec['task']}",
            when,
        )
        self._message(
            thread,
            user,
            ChatMessageRole.ASSISTANT,
            "On it — one Scout, deep. I'll apply your standing rules and your "
            "verify-before-staging procedure, and tell you what they changed.",
            when + timedelta(minutes=1),
        )
        run = self._run(project, user, thread, spec, 0, AgentRunStatus.COMPLETED)
        staged_at = self._at(0, -30)
        items = [self._stage(project, run, f, staged_at) for f in spec["findings"]]
        set_aside = spec["learning"].get("setAside") or []
        applied = spec["learning"].get("applied") or []
        lines = [
            f"**{spec['focus']}** staged {len(items)} findings — they're in your inbox.\n"
        ]
        for row in applied:
            lines.append(f"- {row['effect']} — because of *{row['memory']}*.")
        if set_aside:
            lines.append(
                f"\nI set aside {len(set_aside)}; open the Scout card to see them."
            )
        lines.append(
            "\nWant me to go after counter-evidence next, or should I draft the comparison table?"
        )
        self._message(
            thread,
            user,
            ChatMessageRole.ASSISTANT,
            "\n".join(lines),
            staged_at + timedelta(minutes=1),
        )
        return thread
