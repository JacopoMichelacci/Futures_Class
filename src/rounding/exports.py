"""Deterministic reports and the primary empirical rounding chart."""
import csv
import hashlib
import json
from pathlib import Path

from src.rounding.rules import ROUNDING_METHODS
from src.section0.phase_b import plain

LABELS = {"nearest": "Nearest (half-up)", "toward_zero": "Toward zero (floor)",
          "away_from_zero": "Away from zero (ceil)", "residual_greedy": "Residual greedy"}


def write_csv(path, rows):
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(plain(rows))


def rounding_figure(result):
    """Use the existing Plotly dependency; panels share the same daily-bp metric."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Full robustness grid", "Detail: $1m–$100m"))
    styles = {"nearest": ("#194b74", "solid", "circle-open"),
              "toward_zero": ("#bf6210", "dash", "square"),
              "away_from_zero": ("#25816b", "dashdot", "triangle-up"),
              "residual_greedy": ("#964a9f", "dot", "x")}
    for col in (1, 2):
        for method in ROUNDING_METHODS:
            rows = [r for r in result["by_aum"] if r["rule"] == method
                    and (col == 1 or r["fixed_aum"] <= 100000000)]
            colour, dash, marker = styles[method]
            fig.add_trace(go.Scatter(x=[r["fixed_aum"] for r in rows],
                y=[float(r["tracking_rmse_bps_daily"]) for r in rows],
                name=LABELS[method], legendgroup=method, showlegend=col == 1,
                mode="lines+markers", line=dict(color=colour, dash=dash, width=2),
                marker=dict(symbol=marker, size=10 if method == "nearest" else 7)), row=1, col=col)
        fig.add_vline(x=1000000, line_dash="dot", line_color="#777777", row=1, col=col)
        selected = result["selection"]["preferred_rule"]
        if selected:
            score = next(r["tracking_rmse_bps_daily"] for r in result["summary"] if r["rule"] == selected)
            fig.add_trace(go.Scatter(x=[1000000], y=[float(score)], mode="markers",
                marker=dict(symbol="star", size=17, color="#efc247", line=dict(color="#333333", width=1)),
                name="Selected at $1m: " + LABELS[selected], showlegend=col == 1,
                legendgroup="selection"), row=1, col=col)
        grid = result["core_aum_grid"][:4 if col == 1 else 3]
        fig.update_xaxes(type="log", tickmode="array", tickvals=grid,
            ticktext=["$1m", "$10m", "$100m", "$1bn"][:len(grid)],
            title_text="Fixed AUM (log scale)", row=1, col=col)
        fig.update_yaxes(title_text="Tracking RMSE (daily bp)", gridcolor="#e5e8eb", row=1, col=col)
    fig.update_layout(template="plotly_white", width=1400, height=650,
        title=dict(text="Integer rounding: 148 common dates, 2 March–30 September 2026"
                   "<br><sup>Only rounding varies. $1m selects the rule; other AUMs are robustness checks.</sup>", x=0.03),
        margin=dict(l=75, r=35, t=110, b=150),
        legend=dict(orientation="h", y=-0.23, x=0, font=dict(size=12)))
    return fig


def write_chart_html(result, path):
    """Self-contained offline export using Plotly's browser camera button."""
    rounding_figure(result).write_html(path, include_plotlyjs=True, div_id="rounding-figure",
        config={"toImageButtonOptions": {"format": "png", "filename": "tracking_rmse_vs_aum",
                                        "width": 1400, "height": 650, "scale": 1.5}})


def register_chart_export(directory, png_bytes):
    """Record an explicitly supplied browser export against the current figure."""
    directory = Path(directory)
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Expected an exported PNG")
    (directory / "tracking_rmse_vs_aum.png").write_bytes(png_bytes)
    receipt = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
               for name in ("rounding_figure.json", "tracking_rmse_vs_aum.png")}
    (directory / "chart_export.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def chart_export_matches(directory):
    directory = Path(directory)
    try:
        receipt = json.loads((directory / "chart_export.json").read_text())
        return all(receipt[name] == hashlib.sha256((directory / name).read_bytes()).hexdigest()
                   for name in ("rounding_figure.json", "tracking_rmse_vs_aum.png"))
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return False


def write_outputs(result, output_dir, include_chart=True):
    directory = Path(output_dir); directory.mkdir(parents=True, exist_ok=True)
    selection = result["selection"]
    summary = [dict(r, selected=r["rule"] == selection["preferred_rule"],
                    within_tie_tolerance=r["rule"] in selection["tied_rules"],
                    delta_from_minimum_bps=r["tracking_rmse_bps_daily"] - selection["minimum_rmse_bps"])
               for r in result["summary"]]
    write_csv(directory / "rounding_summary.csv", summary)
    write_csv(directory / "rounding_by_aum.csv", result["by_aum"])
    write_csv(directory / "rounding_daily.csv", result["daily"])
    write_csv(directory / "rounded_holdings.csv", result["holdings"])
    if include_chart:
        (directory / "rounding_figure.json").write_text(rounding_figure(result).to_json() + "\n")
    table = ["| AUM | Nearest | Toward zero | Away from zero | Residual greedy |",
             "|---|---:|---:|---:|---:|"]
    for aum in result["core_aum_grid"]:
        values = {r["rule"]: r["tracking_rmse_bps_daily"] for r in result["by_aum"] if r["fixed_aum"] == aum}
        table.append("| $" + format(aum, ",") + " | " + " | ".join(f"{values[m]:.9f}" for m in ROUNDING_METHODS) + " |")
    selected = selection["preferred_rule"]
    status = "PERSON 4 ROUNDING HANDOFF READY" if selected else "BLOCKED: unresolved floor/ceil tie"
    differences = {aum: next(r["positions_different_from_nearest"] for r in result["by_aum"]
                              if r["fixed_aum"] == aum and r["rule"] == "residual_greedy") for aum in result["core_aum_grid"]}
    lines = ["# Person 4: independent empirical rounding analysis", "", "**" + status + "**", "",
        "Real Databento GLBX.MDP3-derived frozen Section 0 inputs, not synthetic fixture results.", "",
        "## Frozen experiment", "",
        "- Issue #8; only integer rounding changes. Destination, -5 baseline timing, baseline capacity, weights, benchmark and accounting remain fixed.",
        "- 148 identical common return dates: 2 March–30 September 2026. Initial decision: 27 February; earlier data are warm-up only.",
        "- Reference AUM: $1m, with no binding capacity in the frozen reference targets. Core grid: $1m/$10m/$100m/$1bn. No $250k fallback is activated.",
        "- Exact post-capacity fractional targets and ceilings are consumed without re-sizing or reapplying capacity. Nearest reproduces frozen baseline holdings and daily returns exactly.",
        "- Pre-existing exact-contract holdings, publication-safe provenance and a shared fixed-AUM denominator are enforced. Every method must retain the full frozen mask before any metric is computed.", "",
        "## Results", "", "Tracking RMSE in daily basis points, without annualisation. CSVs retain full decimal precision; this table is rounded for display.", "", *table, "",
        "## Reference-only selection", "",
        "Preferred rule at $1m: **" + (selected or "unresolved") + "**.",
        "Tie rule triggered: **" + str(selection["tie_triggered"]) + "**. Rules strictly within 0.1 daily bp of the minimum: " + ", ".join(selection["tied_rules"]) + ".",
        "Selection reason: `" + selection["reason"] + "`. Exactly 0.1 bp is not a tie. Other AUMs do not select the preferred rule.", "",
        "## Interpretation and limitations", "",
        "Residual greedy has no shared notional-budget constraint. Its locked squared dollar-notional objective is separable; exact-half targets are the principal distinction from nearest-half-up because a zero-improvement addition is rejected. The engine is unchanged.",
        "Observed greedy-vs-nearest position differences (diagnostic only): " + ", ".join(f"${a:,}: {n}/444" for a, n in differences.items()) + ".",
        "Absolute benchmark tracking error also reflects the baseline maturity/timing/capacity implementation held fixed across methods; it is not a rounding-only error decomposition. At large AUM, capacity truncation may make post-capacity targets integral and collapse differences between rounding rules.",
        "Findings are conditional on this frozen historical sample and implementation assumptions. No transaction costs or collateral yield enter ranking; robustness AUMs are descriptive. No other strand's preferred method is imported, and Section 5 has not been run.", "",
        "## Reproduction and outputs", "", "```sh", "uv sync --locked",
        "uv run --no-sync python -B scripts/run_person4.py --real", "```", "",
        "The CLI regenerates numeric outputs and the Plotly specification. The PNG is a browser export, not rerendered by Python. For a fresh export without extra packages, use `--chart-html personal/rounding_chart.html`, open that self-contained file offline and use the camera button. Register the downloaded current-figure PNG with `--chart-png PATH`. The receipt detects later changes to either the figure specification or PNG; it does not independently certify image content.", "",
        "Canonical notebook: [Section 4.ipynb](../../Section%204%20-%20Rounding%20(Alan)/Section%204.ipynb).",
        "- `rounding_summary.csv`: reference-AUM results and tie/selection flags.",
        "- `rounding_by_aum.csv`: all 16 rule/AUM results and descriptive diagnostics.",
        "- `rounding_daily.csv`: common-date returns and tracking differences for reconciliation.",
        "- `rounded_holdings.csv`: exact implemented quantities and decision provenance.",
        "- `tracking_rmse_vs_aum.png`: primary comparison chart; selection point is $1m only.",
        "- `rounding_figure.json` and `chart_export.json`: Plotly specification and exported-image hash receipt.",
        "- `analysis_manifest.json`: frozen input hashes, selection and output hashes.", "",
        "The `--fixtures` CLI mode remains a separate synthetic Part 2 demonstration and is not used for these outputs.", ""]
    (directory / "HANDOFF.md").write_text("\n".join(lines))
    manifest = {k: result[k] for k in ("data_kind", "dates", "core_aum_grid", "selection", "input_hashes", "calendar_id", "catalogue_id")}
    names = ["rounding_summary.csv", "rounding_by_aum.csv", "rounding_daily.csv", "rounded_holdings.csv", "HANDOFF.md"]
    if include_chart:
        names.append("rounding_figure.json")
        manifest["chart_export_verified"] = chart_export_matches(directory)
        if manifest["chart_export_verified"]:
            names.extend(["tracking_rmse_vs_aum.png", "chart_export.json"])
    manifest["output_hashes"] = {n: hashlib.sha256((directory / n).read_bytes()).hexdigest() for n in names}
    (directory / "analysis_manifest.json").write_text(json.dumps(plain(manifest), indent=2, sort_keys=True) + "\n")
    return manifest
