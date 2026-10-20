"""Issue #14 Part 3: run only after Section 0 frozen historical handoff."""
import argparse

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--fixtures", action="store_true")
    args = parser.parse_args()
    if args.real and args.fixtures:
        parser.error("Choose real or fixtures, never both")
    if args.real:
        parser.error("Section 0 Phase B historical adapter is not yet available. "
                     "Do not invent data or overwrite real results.")
    if args.fixtures:
        parser.error("Synthetic end-to-end fixture adapter is not yet connected. "
                     "Run tests/unit/test_selection_analysis.py instead.")
    parser.error("Choose --real after historical handoff; currently unavailable.")

if __name__ == "__main__":
    main()
