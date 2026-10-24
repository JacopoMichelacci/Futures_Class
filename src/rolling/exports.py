"""Real-data timing outputs (Issue #7): CSVs, generated HANDOFF, manifest and Plotly charts.

Charts use the existing Plotly dependency. PNGs are browser exports from the
offline HTML (camera button) registered with a hash receipt, as in Section 4.
"""
import csv
import hashlib
import json
from pathlib import Path

from src.rolling.rules import RULES
from src.section0.phase_b import plain

LABELS = {"fixed_-5": "Fixed -5 (baseline)", "fixed_-10": "Fixed -10",
          "volume_crossover": "Volume crossover", "oi_crossover": "OI crossover"}
CHARTS = {"tracking_rmse_by_roll_rule": "rmse", "roll_trigger_distribution": "triggers"}
TITLE_SUFFIX = "$10m, 148 common dates, 2 March–30 September 2026"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path, rows):
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(plain(rows))


def summary_rows(result):
    selection = result["selection"]
    best = min(r["tracking_rmse_bps"] for r in result["summary"])
    return [{"rule": r["rule"], "tracking_rmse_bps_daily": r["tracking_rmse_bps"],
             "delta_from_minimum_bps": r["tracking_rmse_bps"] - best,
             "common_valid_dates": r["common_valid_dates"], "candidate_dates": r["candidate_dates"],
             "survival": result["gates"]["survival"], "roll_events": r["roll_events"],
             "fallback_events": r["fallback_events"], "fallback_frequency": r["fallback_frequency"],
             "mean_sessions_before_anchor": r["mean_sessions_before_anchor"],
             "within_tie_tolerance": r["rule"] in selection["tied_rules"],
             "selected": r["rule"] == selection["preferred_rule"], "fixed_aum": result["daily"][0].fixed_aum,
             "data_kind": result["label"]} for r in result["summary"]]


def event_rows(result):
    rows = []
    for rule, decisions in result["decisions"].items():
        for d in decisions:
            trigger = d["trigger"] or {}
            rows.append({"rule": rule, "commodity": d["commodity"], "outgoing_contract": d["outgoing_contract"],
                         "incoming_contract": d["incoming_contract"], "anchor": d["anchor"],
                         "search_start": d["search_start"], "search_deadline": d["search_deadline"],
                         "roll_decision_date": d["roll_decision_date"],
                         "first_incoming_interval_end": d["first_incoming_interval_end"],
                         "sessions_before_anchor": d["sessions_before_anchor"], "fallback_flag": d["fallback_flag"],
                         # Diagnostic: the incoming already led on the first permitted session.
                         "triggered_on_first_window_session": (not d["fallback_flag"]
                                                               and d["roll_decision_date"] == d["search_start"]),
                         "trigger_source_date": trigger.get("source_date"),
                         "trigger_outgoing_value": trigger.get("outgoing_value"),
                         "trigger_incoming_value": trigger.get("incoming_value")})
    return rows


def daily_rows(result):
    rows = []
    for d in result["daily"]:
        row = {"date": d.date, "decision_date": d.decision_date, "common_valid_date": d.common_valid,
               "benchmark_daily_return": d.returns["benchmark"] if d.returns else None}
        for rule in RULES:
            value = d.returns[f"timing:{rule}"] if d.returns else None
            row[f"{rule}_daily_return"] = value
            row[f"{rule}_tracking_difference"] = value - d.returns["benchmark"] if d.returns else None
        rows.append(row)
    return rows


def rmse_figure(result):
    import plotly.graph_objects as go
    rows = summary_rows(result)
    selected = result["selection"]["preferred_rule"]
    best = float(min(r["tracking_rmse_bps_daily"] for r in rows))
    top = max(float(r["tracking_rmse_bps_daily"]) for r in rows)
    fig = go.Figure(go.Bar(
        x=[LABELS[r["rule"]] for r in rows], y=[float(r["tracking_rmse_bps_daily"]) for r in rows],
        marker_color=["#bf6210" if r["rule"] == selected else "#194b74" for r in rows],
        text=[f"{r['tracking_rmse_bps_daily']:.3f}" for r in rows], textposition="outside",
        textfont=dict(size=14), cliponaxis=False))
    # Shaded tie zone (no line), so bar value labels stay readable.
    fig.add_hrect(y0=best, y1=best + 0.1, fillcolor="#efc247", opacity=0.35, line_width=0, layer="below")
    fig.add_annotation(xref="paper", x=1, y=top * 1.13, xanchor="right", showarrow=False,
                       text="Shaded: tie zone, within 0.1 bp of the best rule", font=dict(size=12, color="#555555"))
    fig.update_layout(template="plotly_white", width=1100, height=600,
        title=dict(x=0.03, text=f"Roll timing: Tracking RMSE by rule ({TITLE_SUFFIX})"
                   "<br><sup>Lower is better. Only roll timing varies; same baseline pairs, destination, sizing, "
                   "capacity, rounding and benchmark. Orange = selected.</sup>"),
        yaxis=dict(title="Tracking RMSE (daily bp)", range=[0, top * 1.18]), xaxis=dict(title="Timing rule"),
        margin=dict(l=75, r=35, t=110, b=80), showlegend=False)
    return fig


COMMODITY_STYLE = {"ZC": ("#bf6210", -0.22), "ZS": ("#194b74", 0.0), "ZW": ("#25816b", 0.22)}


def _kind(e):
    if e["fallback_flag"]:
        return "x"
    return "diamond" if e["triggered_on_first_window_session"] else "circle"


def trigger_figure(result):
    import plotly.graph_objects as go
    events = event_rows(result)
    # Spread points: commodity offset within each rule, plus a small nudge for identical values.
    groups = {}
    for e in events:
        groups.setdefault((e["rule"], e["commodity"], e["sessions_before_anchor"]), []).append(e)
    x = {}
    for members in groups.values():
        for k, e in enumerate(members):
            x[id(e)] = (RULES.index(e["rule"]) + COMMODITY_STYLE[e["commodity"]][1]
                        + (k - (len(members) - 1) / 2) * 0.06)
    fig = go.Figure()
    for root, (colour, _) in COMMODITY_STYLE.items():
        chosen = [e for e in events if e["commodity"] == root]
        fig.add_trace(go.Scatter(
            x=[x[id(e)] for e in chosen], y=[e["sessions_before_anchor"] for e in chosen], mode="markers",
            name=root, marker=dict(color=colour, size=13, symbol=[_kind(e) for e in chosen],
                                   line=dict(width=1, color="white")),
            hovertext=[f"{LABELS[e['rule']]}: {e['outgoing_contract']} → {e['incoming_contract']}, decided "
                       f"{e['roll_decision_date']}" for e in chosen], hoverinfo="text+y"))
    for name, symbol in (("Crossover", "circle"), ("Crossover on first permitted session", "diamond"),
                         ("Fallback to -5 (no crossover)", "x")):
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=name,
                                 marker=dict(color="#888888", size=12, symbol=symbol)))
    fig.add_hline(y=5, line_dash="dot", line_color="#777777", annotation_text="Baseline -5",
                  annotation_position="bottom left")
    fig.update_layout(template="plotly_white", width=1100, height=600,
        title=dict(x=0.03, text=f"When each rule rolled: sessions before the outgoing anchor ({TITLE_SUFFIX})"
                   "<br><sup>10 baseline roll events per rule; colour = commodity, shape = how the roll was "
                   "triggered. Diagnostic only, not a ranking metric.</sup>"),
        yaxis=dict(title="Sessions before anchor (observed trading sessions)", rangemode="tozero"),
        xaxis=dict(title="Timing rule", tickmode="array", tickvals=list(range(len(RULES))),
                   ticktext=[LABELS[r] for r in RULES], range=[-0.5, len(RULES) - 0.5]),
        legend=dict(orientation="h", y=-0.18, x=0), margin=dict(l=75, r=35, t=110, b=120))
    return fig


FIGURES = {"tracking_rmse_by_roll_rule": rmse_figure, "roll_trigger_distribution": trigger_figure}


def write_chart_html(result, directory):
    """Self-contained offline pages; the camera button downloads the expected PNG name."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, build in FIGURES.items():
        fig = build(result)
        fig.write_html(directory / f"{name}.html", include_plotlyjs=True, config={"toImageButtonOptions": {
            "format": "png", "filename": name, "width": fig.layout.width, "height": fig.layout.height, "scale": 1.5}})


def register_chart_exports(directory, png_paths):
    """Copy browser-exported PNGs (matched by file name) and record a hash receipt."""
    directory = Path(directory)
    for png in map(Path, png_paths):
        if png.stem not in FIGURES or not png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Expected one of {sorted(FIGURES)} as an exported PNG: {png.name}")
        (directory / f"{png.stem}.png").write_bytes(png.read_bytes())
    receipt = {f"{n}{ext}": _sha(directory / f"{n}{ext}") for n in FIGURES for ext in (".json", ".png")
               if (directory / f"{n}{ext}").exists()}
    (directory / "chart_export.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def chart_exports_verified(directory):
    directory = Path(directory)
    try:
        receipt = json.loads((directory / "chart_export.json").read_text())
        return all(receipt[f"{n}{ext}"] == _sha(directory / f"{n}{ext}") for n in FIGURES for ext in (".json", ".png"))
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return False


def handoff_text(result, events):
    selection, gates = result["selection"], result["gates"]
    rows = summary_rows(result)
    selected = selection["preferred_rule"]
    ordered = sorted(rows, key=lambda r: r["tracking_rmse_bps_daily"])
    margin = ordered[1]["tracking_rmse_bps_daily"] - ordered[0]["tracking_rmse_bps_daily"]
    early = [e for e in events if e["triggered_on_first_window_session"]]
    table = ["| Rule | Tracking RMSE (daily bp) | Δ from minimum | Fallbacks to -5 | Mean sessions before anchor |",
             "|---|---:|---:|---:|---:|"]
    table += [f"| {LABELS[r['rule']]} | {r['tracking_rmse_bps_daily']:.6f} | {r['delta_from_minimum_bps']:.6f} | "
              f"{r['fallback_events']}/{r['roll_events']} | {r['mean_sessions_before_anchor']:.2f} |" for r in rows]
    status = "PERSON 2 TIMING HANDOFF READY" if selected else f"BLOCKED: {selection.get('blocked_reason', 'no selection')}"
    return "\n".join([
        "# Person 2: independent empirical roll-timing analysis", "", f"**{status}**", "",
        "Real Databento GLBX.MDP3-derived frozen Section 0 inputs, not synthetic fixture results.", "",
        "## Frozen experiment", "",
        "- Issue #7. Only roll timing changes. The 10 baseline outgoing/incoming pairs, next-deferred destination, "
        "baseline capacity and nearest rounding (shared `holdings_from_contract_path`), weights, benchmark and "
        "accounting are fixed.",
        f"- {gates['common_valid_dates']} common return dates (2 March–30 September 2026; first decision 27 February), "
        f"survival {gates['survival']}; gates (≥{gates['min_common_dates']} dates, ≥{gates['min_survival']}) "
        f"{'pass' if gates['passed'] else 'FAIL'}.",
        f"- Reference AUM ${int(result['daily'][0].fixed_aum):,}: Section 0's activated timing fallback "
        "(capacity binds at $100m).",
        "- Fixed -5 reproduces the frozen Section 0 baseline holdings and daily returns exactly; crossover "
        "decisions reconcile to `data/section0/timing_inputs.parquet`.",
        "- Crossover search (Issue #23): first session the outgoing contract is held through strictly before the "
        "-5 session, first strictly-greater publication-safe observation, flagged -5 fallback.", "",
        "## Results", "", *table, "",
        "## Selection", "",
        f"Preferred timing rule at the $10m reference: **{LABELS[selected] if selected else 'none'}**.",
        f"Margin over the runner-up ({LABELS[ordered[1]['rule']]}): {margin:.6f} daily bp. Tie rule triggered: "
        f"**{len(selection['tied_rules']) > 1}** (ties are strictly below 0.1 bp; order agreed before the real "
        "run: -5, -10, volume, OI).", "",
        "## Interpretation and limitations", "",
        "- Every alternative tracked the 60-session benchmark more closely than rolling at -5, consistent with "
        "earlier rolls moving holdings toward the benchmark's longer target maturity sooner.",
        f"- {len(early)} crossover roll(s) fired on the first permitted session: "
        + (", ".join(f"{LABELS[e['rule']]} {e['outgoing_contract']}→{e['incoming_contract']} "
                     f"({e['sessions_before_anchor']} sessions early)" for e in early) or "none")
        + ". The incoming contract already out-traded the thin September contract, so these rules largely bypass "
        "it. This follows the pre-agreed rule, but it overlaps with destination behaviour and contributes to "
        "the crossover results.",
        "- Timing is evaluated with destination fixed at next deferred; interaction with other destination rules "
        "is not tested here (Section 5 combines strands once, without re-optimising).",
        "- Findings are conditional on this frozen sample; no transaction costs or collateral yield; no other "
        "strand's preferred method is imported.", "",
        "## Reproduction and outputs", "", "```sh", "uv sync --locked",
        "uv run --no-sync python -B scripts/run_person2.py --real", "```", "",
        "PNGs are browser exports: `--chart-html DIR` writes offline pages; use each page's camera button, then "
        "`--chart-png PATH [PATH]` to register them. The receipt detects later figure or image changes.", "",
        "Canonical notebook: [Section 2.ipynb](../../Section%202%20-%20Roll%20Timing%20(Lucie)/Section%202.ipynb).",
        "- `roll_timing_summary.csv`: per-rule RMSE, coverage, fallback and tie/selection flags.",
        "- `roll_events_by_rule.csv`: every roll decision, window and trigger evidence.",
        "- `roll_timing_daily.csv`: daily returns and tracking differences on the common mask.",
        "- `tracking_rmse_by_roll_rule.png`, `roll_trigger_distribution.png`: charts (+ `.json` specs, "
        "`chart_export.json` receipt).",
        "- `analysis_manifest.json`: frozen input hashes, selection and output hashes.", "",
        "`outputs/person2/synthetic_fixture/` is a separate synthetic harness demonstration, not a finding.", ""])


def write_outputs(result, output_dir, input_hashes):
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    events = event_rows(result)
    write_csv(directory / "roll_timing_summary.csv", summary_rows(result))
    write_csv(directory / "roll_events_by_rule.csv", events)
    write_csv(directory / "roll_timing_daily.csv", daily_rows(result))
    for name, build in FIGURES.items():
        (directory / f"{name}.json").write_text(build(result).to_json() + "\n")
    (directory / "HANDOFF.md").write_text(handoff_text(result, events))
    names = ["roll_timing_summary.csv", "roll_events_by_rule.csv", "roll_timing_daily.csv", "HANDOFF.md",
             *(f"{n}.json" for n in FIGURES)]
    verified = chart_exports_verified(directory)
    if verified:
        names += [*(f"{n}.png" for n in FIGURES), "chart_export.json"]
    manifest = {"data_kind": result["label"], "dates": [result["daily"][0].date, result["daily"][-1].date],
                "date_count": len(result["daily"]), "fixed_aum": result["daily"][0].fixed_aum,
                "gates": result["gates"], "selection": result["selection"], "section0_output_hashes": input_hashes,
                "chart_export_verified": verified,
                "output_hashes": {n: _sha(directory / n) for n in names}}
    (directory / "analysis_manifest.json").write_text(json.dumps(plain(manifest), indent=2, sort_keys=True) + "\n")
    return manifest
