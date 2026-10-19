# Person 2 (roll timing) handoff — Issue #7

**Status: no real-data result yet.** The preferred timing rule for Section 5 is **not selected**. The comparison pipeline is complete and tested, but the frozen Section 0 real-data handoff (#4/#5) is not available yet.

- `synthetic_fixture/` holds output from the synthetic matched harness only. It demonstrates the output format and the selection mechanics; it is **not a finding** and must not feed Section 5.
- The real outputs (`roll_timing_summary.csv`, `roll_events_by_rule.csv`) will be written to this folder from the frozen handoff at the $100m reference AUM.

Design and remaining steps: [docs/roll_timing_part3.md](../../docs/roll_timing_part3.md).
