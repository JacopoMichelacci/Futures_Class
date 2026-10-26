"""Section 5 outputs (outputs/final/): CSVs, matplotlib charts, generated HANDOFF and manifest."""
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from src.section0.phase_b import plain

OWNERS = {"destination": "Varshini", "timing": "Lucie", "capacity": "Jacopo", "rounding": "Alan"}
LABELS = {"target_75": "75-session target", "oi_crossover": "OI crossover",
          "overflow_sequential_two": "Sequential two-deferred overflow", "toward_zero": "Toward zero"}
AUM_LABEL = {1000000: "$1m", 10000000: "$10m", 100000000: "$100m", 1000000000: "$1bn"}
SAMPLE = "148 common dates, 2 March–30 September 2026"


def _csv(path, rows):
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(plain(rows))


def _strand_rows(outputs_dir):
    """Each strand's selected rule vs its baseline at its own reference AUM (from its committed CSVs)."""
    out = Path(outputs_dir)

    def read(name):
        with (out / name).open() as stream:
            return list(csv.DictReader(stream))

    d = {r["rule"]: Decimal(r["tracking_rmse_bps"]) for r in read("person1/contract_destination_summary.csv")}
    t = {r["rule"]: Decimal(r["tracking_rmse_bps_daily"]) for r in read("person2/roll_timing_summary.csv")}
    c = {r["method"]: Decimal(r["tracking_rmse_bps"]) for r in read("person3/capacity_summary.csv")
         if int(r["aum_usd"]) == 1000000000}
    r = {x["rule"]: Decimal(x["tracking_rmse_bps_daily"]) for x in read("person4/rounding_summary.csv")}
    return [("destination", 10000000, d["next_deferred"], d["target_75"], "next deferred"),
            ("timing", 10000000, t["fixed_-5"], t["oi_crossover"], "fixed -5"),
            ("capacity", 1000000000, c["hard_truncate"], c["overflow_sequential_two"], "hard truncate"),
            ("rounding", 1000000, r["nearest"], r["toward_zero"], "nearest")]


def selection_rows(result, outputs_dir):
    reproduced = {x["strand"]: x["reproduced_rmse_bps"] for x in result["reconciliation"]}
    return [{"strand": strand, "owner": OWNERS[strand], "selected_rule": result["selections"][strand],
             "reference_aum": aum, "baseline_rule": base_name, "strand_baseline_rmse_bps": base,
             "strand_selected_rmse_bps": chosen, "improvement_bps": base - chosen,
             "reproduced_in_section5_rmse_bps": reproduced[strand],
             "reproduced_matches": abs(reproduced[strand] - chosen) < Decimal("1e-6")}
            for strand, aum, base, chosen, base_name in _strand_rows(outputs_dir)]


def summary_rows(result):
    rows = [{**s, "status": "evaluated", "reason": "",
             "destination_timing_only_rmse_bps": result["destination_timing_baseline_sizing"][s["fixed_aum"]]}
            for s in result["summary"]]
    rows += [{"fixed_aum": u["fixed_aum"], "baseline_rmse_bps": None, "integrated_rmse_bps": None,
              "change_bps": None, "change_pct": None, "common_valid_dates": None, "candidate_dates": None,
              "capacity_binding_targets": None, "unimplemented_notional": None, "integrated_contracts_held": None,
              "status": "not_evaluable", "reason": u["reason"], "destination_timing_only_rmse_bps": None}
             for u in result["unavailable"]]
    return sorted(rows, key=lambda r: r["fixed_aum"])


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 12, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def strand_chart(rows, path):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = range(len(rows))
    base = [float(r["strand_baseline_rmse_bps"]) for r in rows]
    chosen = [float(r["strand_selected_rmse_bps"]) for r in rows]
    ax.bar([i - 0.2 for i in x], base, 0.4, color="#9aa5b1", label="Strand baseline rule")
    ax.bar([i + 0.2 for i in x], chosen, 0.4, color="#194b74", label="Selected rule")
    for i in x:
        ax.text(i - 0.2, base[i], f"{base[i]:.2f}", ha="center", va="bottom", fontsize=10)
        ax.text(i + 0.2, chosen[i], f"{chosen[i]:.2f}", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(list(x), [f"{r['strand'].title()} ({r['owner']})\n{LABELS[r['selected_rule']]}\n"
                            f"at {AUM_LABEL[r['reference_aum']]}" for r in rows])
    ax.set_yscale("log")
    ax.set_ylabel("Tracking RMSE (daily bp, log scale)")
    ax.set_title("Each strand's selected rule beat its baseline on its own\n"
                 f"(each at its frozen reference AUM; {SAMPLE})", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def integrated_chart(rows, path):
    plt = _plt()
    evaluated = [r for r in rows if r["status"] == "evaluated"]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = range(len(evaluated))
    base = [float(r["baseline_rmse_bps"]) for r in evaluated]
    integ = [float(r["integrated_rmse_bps"]) for r in evaluated]
    ax.bar([i - 0.2 for i in x], base, 0.4, color="#9aa5b1", label="Original baseline")
    ax.bar([i + 0.2 for i in x], integ, 0.4, color="#bf6210", label="Integrated (all four selected rules)")
    for i, r in enumerate(evaluated):
        ax.text(i - 0.2, base[i], f"{base[i]:.2f}", ha="center", va="bottom", fontsize=10)
        ax.text(i + 0.2, integ[i], f"{integ[i]:.2f}\n({float(r['change_pct']):+.0f}%)", ha="center",
                va="bottom", fontsize=10)
    ax.set_xticks(list(x), [AUM_LABEL[r["fixed_aum"]] for r in evaluated])
    ax.set_ylim(0, max(base + integ) * 1.3)
    ax.set_xlabel("Fixed AUM")
    ax.set_ylabel("Tracking RMSE (daily bp)")
    missing = [AUM_LABEL[r["fixed_aum"]] for r in rows if r["status"] != "evaluated"]
    note = f"\n{', '.join(missing)} not evaluable: a required overflow contract is outside the frozen data." if missing else ""
    ax.set_title("Section 5: original baseline vs integrated implementation (lower is better)\n"
                 f"Same benchmark, AUM denominator and {SAMPLE}.{note}", loc="left", fontsize=12)
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def maturity_chart(result, path):
    """Sessions to first notice of the held contract: baseline vs integrated, per commodity."""
    plt = _plt()
    curve = {(r["date"], r["contract"]): r["days_to_anchor"] for r in result["inputs"].curve}
    base = {(p["decision_date"], p["commodity"]): p["contract"] for p in result["inputs"].baseline_path.positions}
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.8), sharey=True)
    import datetime as dt
    import matplotlib.dates as mdates
    for ax, root in zip(axes, ("ZC", "ZS", "ZW")):
        rows = sorted((p for p in result["path"] if p["commodity"] == root), key=lambda p: p["decision_date"])
        days = [dt.date.fromisoformat(p["decision_date"]) for p in rows]
        ax.plot(days, [curve[(p["decision_date"], base[(p["decision_date"], root)])] for p in rows],
                color="#9aa5b1", lw=2, label="Baseline")
        ax.plot(days, [curve[(p["decision_date"], p["contract"])] for p in rows], color="#bf6210", lw=2,
                label="Integrated")
        ax.axhline(60, color="#194b74", ls="--", lw=1.5, label="Benchmark target (60)")
        ax.set_title(root)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    axes[0].set_ylabel("Sessions to first notice of held contract")
    axes[0].legend(frameon=False, fontsize=10)
    fig.suptitle("Why: combined, destination + timing push holdings past the benchmark's 60-session maturity "
                 f"(mean {result['mean_days_to_fnd']['integrated']:.0f} vs baseline "
                 f"{result['mean_days_to_fnd']['baseline']:.0f})", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def handoff_text(result, selections, summary):
    fmt = lambda v: "—" if v is None else f"{v:.6f}"
    lines = ["# Section 5: integrated portfolio", "",
             "**Real Databento GLBX.MDP3-derived frozen Section 0 inputs.** The four independently selected rules "
             "are combined once, in the locked order (destination → timing → capacity → rounding). Nothing is "
             "re-optimised or reselected.", "",
             "## Selected rules (read from each strand's committed outputs)", "",
             "| Strand | Owner | Selected rule | Reference AUM | Strand baseline → selected (bp) | Reproduced here |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['strand'].title()} | {r['owner']} | {LABELS[r['selected_rule']]} | "
              f"{AUM_LABEL[r['reference_aum']]} | {r['strand_baseline_rmse_bps']:.4f} → "
              f"{r['strand_selected_rmse_bps']:.4f} | {'yes' if r['reproduced_matches'] else 'NO'} |"
              for r in selections]
    lines += ["", "## Baseline vs integrated (Tracking RMSE, daily bp)", "",
              "| AUM | Baseline | Integrated | Change | Destination + timing only (baseline sizing) | Status |",
              "|---|---:|---:|---:|---:|---|"]
    for r in summary:
        change = "—" if r["change_pct"] is None else f"{r['change_pct']:+.1f}%"
        lines.append(f"| {AUM_LABEL[r['fixed_aum']]} | {fmt(r['baseline_rmse_bps'])} | "
                     f"{fmt(r['integrated_rmse_bps'])} | {change} | {fmt(r['destination_timing_only_rmse_bps'])} | "
                     f"{r['status'] if r['status'] == 'evaluated' else 'not evaluable: ' + r['reason']} |")
    m = result["mean_days_to_fnd"]
    early = [e for e in result["events"] if e["triggered_on_first_window_session"]]
    lines += ["", "## Conclusion", "",
              "Each selected rule improved tracking on its own, but the integrated implementation tracks the "
              "benchmark **less** closely than the original baseline at every evaluable AUM. Combining the "
              "rules without re-optimising produces an interaction: the 75-session destination already moves "
              "holdings out the curve, and OI crossover then rolls on again early. Mean sessions to first notice "
              f"of the held contract: baseline {m['baseline']:.1f}, destination alone {m['destination_only']:.1f}, "
              f"integrated {m['integrated']:.1f}, against the benchmark's 60.", "",
              "## Integration anchor and interactions (descriptive only)", "",
              "- Team decision on #16 (Option A): the destination rule's scheduled roll date is the timing deadline "
              "and fallback. OI crossover searches causally from the first session the outgoing contract is held "
              "in the destination path up to, strictly before, that date. After the first roll this deadline is not "
              "'-5' from the outgoing contract's own first notice date.",
              f"- {len(early)} roll(s) fired on the first permitted session: "
              + ", ".join(f"{e['outgoing_contract']}→{e['incoming_contract']} "
                          f"({e['sessions_before_scheduled_date']} sessions before the scheduled date)" for e in early)
              + ". Corn and soybeans therefore move roughly from May to December/November in late April.",
              "- Each strand's winner alone, run through this same pipeline, reproduces its Part 3 result "
              "(table above), so the difference comes from the combination, not the implementation.", "",
              "## Limitations", "",
              "- $1bn is not evaluable: sequential overflow from the integrated ZSF7/ZSH7 positions needs ZSK7, which "
              "is outside the frozen Section 0 statistics universe. No substitute data were invented.",
              "- Findings are conditional on this frozen sample. There are no transaction costs or collateral "
              "yield, and strand selections were made independently at different reference AUMs.", "",
              "## Reproduction", "", "```sh", "uv sync --locked",
              "uv run --no-sync python -B scripts/run_section5.py", "```", "",
              "Notebook: [Section 5.ipynb](../../Section%205%20-%20Integrated%20Portfolio/Section%205.ipynb).", ""]
    return "\n".join(lines)


def write_outputs(result, outputs_dir, out_dir):
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    selections, summary = selection_rows(result, outputs_dir), summary_rows(result)
    _csv(directory / "strand_selections.csv", selections)
    _csv(directory / "integrated_summary.csv", summary)
    _csv(directory / "baseline_vs_integrated_daily.csv", result["daily"])
    _csv(directory / "integrated_roll_events.csv", list(result["events"]))
    _csv(directory / "integrated_holdings.csv", result["holdings"])
    strand_chart(selections, directory / "strand_selected_vs_baseline.png")
    integrated_chart(summary, directory / "tracking_rmse_baseline_vs_integrated_by_aum.png")
    maturity_chart(result, directory / "maturity_baseline_vs_integrated.png")
    (directory / "HANDOFF.md").write_text(handoff_text(result, selections, summary))
    names = sorted(p.name for p in directory.iterdir() if p.name != "analysis_manifest.json")
    manifest = {"data_kind": "real_frozen_databento_section0", "selections": result["selections"],
                "dates": [result["dates"][0], result["dates"][-1]], "date_count": len(result["dates"]),
                "unavailable": result["unavailable"], "mean_days_to_fnd": result["mean_days_to_fnd"],
                "section0_output_hashes": result["input_hashes"],
                "output_hashes": {n: hashlib.sha256((directory / n).read_bytes()).hexdigest() for n in names}}
    (directory / "analysis_manifest.json").write_text(json.dumps(plain(manifest), indent=2, sort_keys=True) + "\n")
    return manifest
