"""Slide helpers for the Section 5 presentation notebook: one call renders one graph or result line."""
from pathlib import Path

from src.integration.exports import AUM_LABEL, LABELS

CHARTS = {
    "benchmark": "outputs/final/benchmark_construction.png",
    "destination": "outputs/person1/tracking_rmse_by_rule.png",
    "timing": "outputs/person2/tracking_rmse_by_roll_rule.png",
    "capacity": "outputs/person3/tracking_rmse_vs_aum.png",
    "rounding": "outputs/person4/tracking_rmse_vs_aum.png",
    "alone": "outputs/final/strand_selected_vs_baseline.png",
    "combined": "outputs/final/tracking_rmse_baseline_vs_integrated_by_aum.png",
    "why": "outputs/final/maturity_baseline_vs_integrated.png",
}


def chart(root, name, width=950):
    from IPython.display import Image, display
    display(Image(Path(root) / CHARTS[name], width=width))


def winner(selections, strand):
    """One bold result line from the strand's committed outputs (reproduced in Section 5)."""
    from IPython.display import Markdown, display
    r = next(row for row in selections if row["strand"] == strand)
    display(Markdown(f"### Winner: {LABELS[r['selected_rule']]}: {r['strand_selected_rmse_bps']:.3f} bp\n"
                     f"vs {r['strand_baseline_rmse_bps']:.3f} bp for the baseline ({r['baseline_rule']}), "
                     f"at {AUM_LABEL[r['reference_aum']]}"))
