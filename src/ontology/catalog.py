"""
Ontology catalog: typed metadata describing each ontology object.

The catalog is the single source of truth for:

  1. Allowlisting which physical `onto_*` tables and columns the agent can read.
  2. Producing the compact ontology summary injected into the agent system prompt.
  3. Powering `read_ontology_schema` tool output (no live `information_schema` calls).

Keeping this in Python (instead of inferring from the DB at runtime) means the
agent always sees a stable, human-curated description and we never expose
table/column names the user didn't approve.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OntologyAttribute:
    name: str
    description: str
    data_type: str


@dataclass(frozen=True)
class OntologyObject:
    name: str
    table_name: str
    description: str
    attributes: tuple[OntologyAttribute, ...]

    def attribute_names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.attributes)

    def get_attribute(self, name: str) -> OntologyAttribute | None:
        for attr in self.attributes:
            if attr.name == name:
                return attr
        return None


_REGION = OntologyObject(
    name="region",
    table_name="onto_region",
    description="Sales region dimension. One row per region.",
    attributes=(
        OntologyAttribute("region_id", "Stable region identifier (PK).", "TEXT"),
        OntologyAttribute("region_name", "Human-readable region name.", "TEXT"),
    ),
)

_SEGMENT = OntologyObject(
    name="segment",
    table_name="onto_segment",
    description="Customer segment dimension (e.g. Enterprise, Mid-Market, SMB).",
    attributes=(
        OntologyAttribute("segment_id", "Stable segment identifier (PK).", "TEXT"),
        OntologyAttribute("segment_name", "Human-readable segment name.", "TEXT"),
    ),
)

_ACCOUNT = OntologyObject(
    name="account",
    table_name="onto_account",
    description="Customer account dimension. Derived from deals.account_name.",
    attributes=(
        OntologyAttribute("account_id", "Stable account identifier (PK).", "TEXT"),
        OntologyAttribute("account_name", "Human-readable account name.", "TEXT"),
    ),
)

_MANAGER = OntologyObject(
    name="manager",
    table_name="onto_manager",
    description="Sales manager dimension. One row per manager referenced by reps.",
    attributes=(
        OntologyAttribute("manager_id", "Stable manager identifier (PK).", "TEXT"),
        OntologyAttribute("manager_name", "Human-readable manager name.", "TEXT"),
    ),
)

_REP = OntologyObject(
    name="rep",
    table_name="onto_rep",
    description="Sales representative entity with FKs to manager, region, and segment.",
    attributes=(
        OntologyAttribute("rep_id", "Stable rep identifier (PK).", "TEXT"),
        OntologyAttribute("rep_name", "Human-readable rep name.", "TEXT"),
        OntologyAttribute("hire_date", "Date the rep was hired.", "DATE"),
        OntologyAttribute("manager_id", "FK to onto_manager(manager_id).", "TEXT"),
        OntologyAttribute("region_id", "FK to onto_region(region_id).", "TEXT"),
        OntologyAttribute("segment_id", "FK to onto_segment(segment_id).", "TEXT"),
    ),
)

_REP_QUOTA = OntologyObject(
    name="rep_quota",
    table_name="onto_rep_quota",
    description="Per-rep quota by period (time series). Composite PK (rep_id, period).",
    attributes=(
        OntologyAttribute("rep_id", "FK to onto_rep(rep_id).", "TEXT"),
        OntologyAttribute("period", "Quota period label, e.g. '2026Q1'.", "TEXT"),
        OntologyAttribute("quota", "Quota target for the period (numeric).", "NUMERIC"),
    ),
)

_DEAL = OntologyObject(
    name="deal",
    table_name="onto_deal",
    description="Deal (opportunity) fact table with FKs to account, rep, region, segment.",
    attributes=(
        OntologyAttribute("deal_id", "Stable deal identifier (PK).", "TEXT"),
        OntologyAttribute("account_id", "FK to onto_account(account_id).", "TEXT"),
        OntologyAttribute("rep_id", "FK to onto_rep(rep_id).", "TEXT"),
        OntologyAttribute("region_id", "FK to onto_region(region_id).", "TEXT"),
        OntologyAttribute("segment_id", "FK to onto_segment(segment_id).", "TEXT"),
        OntologyAttribute(
            "stage",
            "Deal stage. Open: Prospecting/Discovery/Proposal/Negotiation. Closed: Closed Won, Closed Lost.",
            "TEXT",
        ),
        OntologyAttribute("deal_value", "Deal amount in USD.", "NUMERIC"),
        OntologyAttribute("close_date", "Expected or actual close date.", "DATE"),
        OntologyAttribute("created_date", "Date the deal was created.", "DATE"),
        OntologyAttribute("product_line", "Product line label.", "TEXT"),
        OntologyAttribute("loss_reason", "Loss reason (only meaningful for Closed Lost).", "TEXT"),
    ),
)


ONTOLOGY_CATALOG: dict[str, OntologyObject] = {
    obj.name: obj
    for obj in (_REGION, _SEGMENT, _ACCOUNT, _MANAGER, _REP, _REP_QUOTA, _DEAL)
}


def list_objects() -> list[OntologyObject]:
    """Return all ontology objects in catalog order."""
    return list(ONTOLOGY_CATALOG.values())


def get_object(name: str) -> OntologyObject | None:
    return ONTOLOGY_CATALOG.get(name)


def object_summary_for_prompt() -> str:
    """Compact, human-readable catalog summary for the agent system prompt."""
    lines: list[str] = ["Ontology objects (allowlisted):"]
    for obj in list_objects():
        attrs = ", ".join(a.name for a in obj.attributes)
        lines.append(f"- {obj.name} ({obj.table_name}): {obj.description}")
        lines.append(f"    attributes: {attrs}")
    return "\n".join(lines)


def schema_dict(obj: OntologyObject) -> dict[str, object]:
    """Catalog schema rendered as a JSON-friendly dict for tool responses."""
    return {
        "name": obj.name,
        "table_name": obj.table_name,
        "description": obj.description,
        "attributes": [
            {
                "name": attr.name,
                "description": attr.description,
                "data_type": attr.data_type,
            }
            for attr in obj.attributes
        ],
    }
