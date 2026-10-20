"""Write Issue #14 outputs, only for explicitly labeled synthetic or real runs."""
from __future__ import annotations
import csv
from pathlib import Path

def _csv(path, rows):
    rows = list(rows)
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def export_results(result, outdir, *, provenance, allow_incomplete=False):
    """Never publish synthetic runs to the canonical real-data output directory."""
    if provenance not in ("synthetic", "real"):
        raise ValueError("Provenance must be explicit")
    outdir = Path(outdir)
    if provenance == "synthetic" and "synthetic" not in outdir.parts:
        raise ValueError("Synthetic output directory must include 'synthetic'")
    if not allow_incomplete and not result["coverage_passed"]:
        raise ValueError("Coverage gate failed; real findings cannot be selected")
    if any(row["mean_maturity_sessions"] is None for row in result["summary"]):
        raise ValueError("Maturity diagnostics required before exporting all outputs")
    try:
        import matplotlib  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("matplotlib missing from shared environment") from exc
    outdir.mkdir(parents=True, exist_ok=True)
    _csv(outdir / "contract_destination_summary.csv", result["summary"])
    _csv(outdir / "contract_destination_daily.csv", result["daily"])
    _plot(result, outdir, provenance)
    with (outdir / "HANDOFF.md").open("w", encoding="utf-8") as f:
        f.write(f"# Person 1 Part 3 — {provenance.upper()}\n\n")
        f.write(f"Common valid dates: {len(result['common_dates'])}\n\n")
        f.write(f"Coverage passed: {result['coverage_passed']}\n\n")
        f.write(f"Preferred rule: {result['preferred_rule'] or 'unresolved'}\n\n")
        f.write("Metric: realized daily return Tracking RMSE (bp).\n\n")
        if provenance == "synthetic":
            f.write("**Synthetic demonstration only. These are not empirical findings.**\n\n")
        if result["tie_unresolved"]:
            f.write("Tie unresolved: Part V tie priority must be confirmed.\n\n")
        f.write("The same benchmark, frozen AUM, and common date mask are required.\n")
        f.write("Review actual Section 0 provenance, contract quantities, roll dates, "
                "capacity, rounding and benchmark before declaring real results.\n")
    return outdir

def _plot(result, outdir, provenance):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is needed for the required PNG outputs; "
                           "coordinate dependency through the shared lockfile") from exc
    rows = result["summary"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar([r["rule"] for r in rows],
           [r["tracking_rmse_bps"] if r["tracking_rmse_bps"] is not None else 0
            for r in rows])
    ax.set_ylabel("Daily tracking RMSE (bp)")
    ax.set_title(f"Contract destination — {provenance}")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(outdir / "tracking_rmse_by_rule.png", dpi=160)
    plt.close(fig)
    # Maturity exposure requires observed-session maturity diagnostics.
    if any(r["mean_maturity_sessions"] is None for r in rows):
        raise ValueError("Cannot plot maturity exposure without real maturity diagnostics")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar([r["rule"] for r in rows],
           [r["mean_maturity_sessions"] for r in rows])
    ax.set_ylabel("Mean observed sessions to anchor")
    ax.set_title(f"Maturity exposure — {provenance}")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(outdir / "maturity_exposure.png", dpi=160)
    plt.close(fig)
