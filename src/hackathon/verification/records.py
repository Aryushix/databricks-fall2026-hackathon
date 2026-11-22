"""Records that cross component boundaries. Plain dataclasses: no extra dependency on Databricks or CI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATUSES = ("CONFIRMED", "LIKELY", "NOT CONFIRMED", "UNVERIFIED", "CONTRADICTED")

ASSERTION_TYPES = (
    "OFFERED",
    "PARTNER_ON_SITE",          # partner-run unit or visiting specialist on the premises (counts, decision 1)
    "OUTSOURCED_OFF_SITE",      # arranged through another provider elsewhere (does not count)
    "REFERRAL_ONLY",
    "PLANNED_OR_COMING_SOON",
    "DISCONTINUED",
    "NOT_OFFERED",
    "EDUCATIONAL_OR_GENERIC",
    "OTHER_ENTITY",             # set in code from entity_mentions, never by the model alone
    "OUTREACH_CAMP",
    "PERSONNEL_ONLY",
    "RESEARCH_OR_LISTING",
    "EXTRACTOR_ARTIFACT",       # the extractor's note about missing content: dropped before scoring
    "AMBIGUOUS",
)


@dataclass(frozen=True)
class Claim:
    """One atomic statement: a list item, a description sentence, or a line of an official page."""

    claim_id: str
    unique_id: str
    field: str                  # description | capability | procedure | equipment | specialties | official_page
    index: int
    text: str
    content_hash: str
    lineage: str                # vf_extraction | official_site
    origin_cluster_id: str      # vf:<unique_id> | official:<domain>
    source_type: str            # extracted_record | official_site_page | official_chain_page
    source_url: str | None = None
    source_date: str | None = None
    retrieval_date: str | None = None
    context: str | None = None  # navigation | blog | None (official pages only)


@dataclass(frozen=True)
class ConceptMatch:
    concept_id: str
    role: str                   # direct | ambiguous | prerequisite | confusable
    sub_id: str                 # synonym, child id, prerequisite id or confusable id
    specificity_kind: str       # exact | child | ambiguous | prerequisite | confusable
    specificity: float
    start: int
    end: int
    matched_text: str
    key: bool = False           # prerequisite is a key prerequisite
    negates_parent: bool = True  # a negative claim on this match contradicts the concept itself


@dataclass
class Classification:
    """What one classifier said about one claim and one concept."""

    assertion_type: str
    quote: str                  # exact substring of the claim text
    classifier_model: str
    classifier_version: str
    scope: str = "all"          # all | subgroup
    temporal: str = "current"   # current | past | future | restarted
    years: tuple[int, ...] = ()
    entity_mentions: tuple[str, ...] = ()
    cues: tuple[str, ...] = ()
    disagreement: bool = False  # rule pass and model disagree
    rule_assertion_type: str | None = None


@dataclass
class Evidence:
    """One claim's contribution to one hospital-treatment verdict, as written into the output."""

    claim_id: str
    quote: str
    field: str
    assertion_type: str
    concept_ids: list[str]
    role: str
    source_url: str | None
    source_type: str
    lineage: str
    origin_cluster_id: str
    source_date: str | None
    retrieval_date: str | None
    entity_match: str
    relevance_score: float
    classifier_model: str
    classifier_version: str
    sub_id: str = ""                # which synonym, child, prerequisite or confusable matched
    specificity: float = 1.0
    key: bool = False
    negates_parent: bool = True
    scope: str = "all"
    temporal: str = "current"
    copied: bool = False
    copy_kind: str | None = None
    copy_cluster_size: int = 0
    stale: bool = False
    weight: float = 0.0             # signed contribution under the scorer, before the per-cluster max
    entity_mentions: list[str] = field(default_factory=list)
    disagreement: bool = False
    rule_assertion_type: str | None = None
    cues: list[str] = field(default_factory=list)

    @property
    def date(self) -> str | None:
        return self.source_date or self.retrieval_date

    def to_dict(self) -> dict:
        return asdict(self)
