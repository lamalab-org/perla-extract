"""Compare saved decision ledgers without loading a historical scoring engine.

The comparison unit is an original input path. Canonical facts can gain aliases
or move between representations after a projection change; retaining those paths
separates changed credit from inventory changes. Reports remain immutable.
"""

from __future__ import annotations


def decisions(report: dict, view: str, side: str) -> dict[str, dict]:
    core = report["core_facts"]
    ledger = core if view == "attributed" else core["recovery"]
    field = "truth" if side == "truth" else "predicted"
    opposite = "prediction" if side == "truth" else "truth"
    partner_field = "predicted" if side == "truth" else "truth"
    aliases = core["fact_sources"][side]
    partners = core["fact_sources"][opposite]
    matched = [m[f"{field}_path"] for m in ledger["matches"]]
    denied = ledger[f"unmatched_{side}_paths"]
    if (
        len(set(matched)) != len(matched)
        or len(set(denied)) != len(denied)
        or set(matched) & set(denied)
        or set(matched) | set(denied) != set(aliases)
        or len(aliases) != ledger["micro"][field]
    ):
        raise ValueError(
            "Saved score does not account for each canonical fact exactly once"
        )
    result = {}
    for match in ledger["matches"]:
        path = match[f"{field}_path"]
        partner = match[f"{partner_field}_path"]
        for source in aliases[path]:
            result[source] = {
                "canonical_path": path,
                "credited": True,
                "partner_paths": sorted(partners[partner]),
            }
    for path in ledger[f"unmatched_{side}_paths"]:
        for source in aliases[path]:
            result[source] = {
                "canonical_path": path,
                "credited": False,
                "partner_paths": [],
            }
    return result


def decision_change(before: dict | None, after: dict | None) -> str:
    if before is None:
        return "added_to_inventory"
    if after is None:
        return "removed_from_inventory"
    if before["credited"] != after["credited"]:
        return "gained_credit" if after["credited"] else "lost_credit"
    if before["partner_paths"] != after["partner_paths"]:
        if set(before["partner_paths"]) & set(after["partner_paths"]):
            return "changed_representation"
        return "changed_assignment"
    if before["canonical_path"] != after["canonical_path"]:
        return "changed_representation"
    return "unchanged"
