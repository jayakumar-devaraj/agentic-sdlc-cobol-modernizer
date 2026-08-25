"""What models exist, what they cost, and which nodes have evidence they are good enough.

ADR-0015. Before this, `config/model_routing.yaml` named a model per (node, tier) directly, which
made three separate things invisible:

1. **Price was nowhere in the repo.** Choosing Opus over Sonnet was a decision about money made
   without the numbers being written down anywhere a reviewer could check them.
2. **"Good enough" was an assertion.** Nothing distinguished a model that had been benchmarked for
   a node from one somebody typed in.
3. **A price change required a human to notice.** Claude Sonnet 5's introductory rate expires
   2026-08-31; with model names hardcoded per tier, nothing would re-evaluate anything when it
   does.

The catalog turns all three into data. Selection (`core/model_routing.py`) then *computes* the
model rather than reading it: the cheapest catalogued model whose `capability_rank` clears the
tier's bar and which is `verified_for` that node.

**This is not the runtime scoring engine ADR-0004 rejected, and the distinction is load-bearing.**
Selection is a pure function of checked-in data: the same catalog and the same policy always yield
the same model, so `design.json` can still answer "which model produced this?" and tests are
reproducible. What is dynamic is that the *inputs* are data rather than a hardcoded name -- edit a
price or add a benchmark result, and every node that qualifies re-selects on the next invocation
without anyone hand-editing fifteen config entries.

**`verified_for` is the honest part and the constraint that matters.** A node may only be routed to
a model that has real benchmark evidence behind it, recorded in `docs/qa/verification-report.md`.
Adding a model to the catalog does not make it eligible; running the benchmark does.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from cobol_modernizer.core.package_data import CONFIG_ROOT

_DEFAULT_CATALOG_PATH = CONFIG_ROOT / "model_catalog.yaml"


class ModelCatalogError(Exception):
    """`config/model_catalog.yaml` is missing, malformed, or internally inconsistent.

    Same fail-loudly posture as `core/model_routing.ModelRoutingConfigError`: a catalog this module
    cannot fully validate must not be partially used, because a silently-dropped entry would show
    up as a node quietly selecting a more expensive model than intended.
    """


class CatalogEntry(BaseModel):
    """One model's real, checkable properties.

    `capability_rank` is a coarse ordering, not a benchmark score -- it exists to express "this
    tier needs at least a Sonnet-class model", not to claim a measurable quality difference between
    adjacent ranks. The actual evidence lives in `verified_for`.
    """

    model_id: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    capability_rank: int
    verified_for: list[str]
    #: Free text, but required: a price with no date is a price nobody can tell is stale.
    price_note: str


class TokenProfile(BaseModel):
    """Measured typical token usage for one (node, tier), used to compare models on real cost.

    Ranking candidates by list price alone would be wrong: output is ~70% of a real
    `spec_extractor` call's cost and ~80% of a `spec_critic` call's, so a model with cheap input
    and expensive output can look better than it is. These numbers come from the live run recorded
    in `docs/qa/verification-report.md`, not from estimates.
    """

    typical_input_tokens: int
    typical_output_tokens: int


def estimated_cost_usd(entry: CatalogEntry, profile: TokenProfile) -> float:
    """What one call to `entry` is expected to cost for work shaped like `profile`.

    Deliberately ignores prompt-cache effects. They are real (measured cache reads run 0.1x input
    price) but depend on invocation order and what else ran recently, and a selection function that
    changed its answer based on cache state would not be reproducible. Systematically overstating
    every candidate by the same factor does not change their ranking.
    """
    return (
        profile.typical_input_tokens * entry.input_usd_per_mtok
        + profile.typical_output_tokens * entry.output_usd_per_mtok
    ) / 1_000_000


def load_catalog(catalog_path: Path = _DEFAULT_CATALOG_PATH) -> dict[str, CatalogEntry]:
    """Load and fully validate the model catalog.

    Raises:
        ModelCatalogError: the file is missing, is not a YAML mapping, has an entry that is not a
            mapping, is missing a required field, or has a non-positive price / non-integer rank.
    """
    try:
        raw = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelCatalogError(f"Could not read model catalog at {catalog_path}: {exc}") from None

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ModelCatalogError(
            f"Model catalog at {catalog_path} is not valid YAML: {exc}"
        ) from None

    if not isinstance(parsed, dict) or not parsed:
        raise ModelCatalogError(
            f"Model catalog at {catalog_path} must be a non-empty mapping of model id to entry"
        )

    catalog: dict[str, CatalogEntry] = {}
    for model_id, entry in parsed.items():
        where = f"{model_id!r} in {catalog_path}"
        if not isinstance(entry, dict):
            raise ModelCatalogError(f"{where} must be a mapping; got {type(entry).__name__}")

        for field in ("input_usd_per_mtok", "output_usd_per_mtok"):
            price = entry.get(field)
            if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
                raise ModelCatalogError(f"{where} has a non-positive or non-numeric {field}: {price!r}")

        rank = entry.get("capability_rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 0:
            raise ModelCatalogError(f"{where} has a non-integer capability_rank: {rank!r}")

        verified_for = entry.get("verified_for")
        if not isinstance(verified_for, list) or any(not isinstance(n, str) for n in verified_for):
            raise ModelCatalogError(f"{where} has a verified_for that is not a list of node names")

        price_note = entry.get("price_note")
        if not isinstance(price_note, str) or not price_note.strip():
            raise ModelCatalogError(
                f"{where} has no price_note; a price with no date or source is one nobody can "
                f"tell is stale"
            )

        catalog[model_id] = CatalogEntry(
            model_id=model_id,
            input_usd_per_mtok=float(entry["input_usd_per_mtok"]),
            output_usd_per_mtok=float(entry["output_usd_per_mtok"]),
            capability_rank=rank,
            verified_for=list(verified_for),
            price_note=price_note,
        )

    return catalog


def select_model(
    catalog: dict[str, CatalogEntry],
    node_name: str,
    min_capability_rank: int,
    profile: TokenProfile,
) -> CatalogEntry:
    """The cheapest catalogued model that clears the bar and is verified for this node.

    Ties (identical estimated cost) break toward the *lower* capability rank, then by model id --
    both deterministic. Preferring the lower rank on a tie is intentional: if two models cost the
    same for this work, the one that is merely sufficient leaves the more capable one's headroom
    for a tier that actually needs it, and keeps the selection stable when a stronger model's price
    later drops to match.

    Raises:
        ModelCatalogError: no catalogued model is both verified for `node_name` and at or above
            `min_capability_rank`. Never falls back to an unverified or under-ranked model -- that
            would silently route a node to something nothing has evidence for, which is exactly
            what `verified_for` exists to prevent.
    """
    eligible = [
        entry
        for entry in catalog.values()
        if node_name in entry.verified_for and entry.capability_rank >= min_capability_rank
    ]
    if not eligible:
        verified = sorted(e.model_id for e in catalog.values() if node_name in e.verified_for)
        raise ModelCatalogError(
            f"No catalogued model is verified for {node_name!r} at capability_rank >= "
            f"{min_capability_rank}. Models verified for that node: {verified or '(none)'}. "
            f"Add a benchmark result to verified_for, or lower the tier's min_capability_rank."
        )
    return min(
        eligible,
        key=lambda e: (estimated_cost_usd(e, profile), e.capability_rank, e.model_id),
    )
