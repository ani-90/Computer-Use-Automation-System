# Evidence

**Start here.** This is the front door — the three things the take-home asks for
("a saved example artifact plus logs from both a discovery run and a replay run;
ideally include one replay that hits an error or exceptional state") are the first
three links below. Everything else in this folder goes further than asked and is
labeled as such.

## The required minimum

1. **Artifact** — [`capabilities/transfer_funds.json`](../capabilities/transfer_funds.json)
   (repo root, not duplicated here — the one file the Replay Engine actually reads).
2. **Discovery run** — [`by-outcome/discovery/SUCCESS/`](by-outcome/discovery/SUCCESS/)
   — the LLM discovering the whole flow from scratch, with `transcript.jsonl` (its own
   reasoning), `trace.json`, screenshots, and `result.json`.
3. **Replay run (happy path)** — [`by-outcome/replay/SUCCESS/`](by-outcome/replay/SUCCESS/)
   — deterministic, `llm_calls: 0`, all outputs populated.
4. **Replay run hitting an exceptional state** —
   [`by-outcome/replay/BUSINESS_OUTCOME_invalid_account/`](by-outcome/replay/BUSINESS_OUTCOME_invalid_account/)
   — a bad `to_account` input, detected and reported as a known answer
   (`business_outcome: "invalid_account"`), not a generic crash.

## Bonus, not required: every other outcome actually produced

[`by-outcome/discovery/`](by-outcome/discovery/) and [`by-outcome/replay/`](by-outcome/replay/)
hold one curated, reviewed example per *distinct outcome* the system actually produced live —
not just the one exceptional state above. Each subfolder is named by the outcome and carries
its own `README.md` explaining what it shows and why that run was picked. See
[`by-outcome/discovery/README.md`](by-outcome/discovery/README.md) and
[`by-outcome/replay/README.md`](by-outcome/replay/README.md) for the full index — it includes
`BUSINESS_OUTCOME_login_rejected`, `POLICY_BLOCK` (an escalation cleanly rejected),
`HARD_FAILURE` (a real click captured alongside a typed "reject" — the engine refuses to guess
which signal is true), and a `SUCCESS` run that combines fault-injected auto-recovery (Phase 8)
with a real human escalation approval (Phase 7) in one run.

## Bonus, not required: the raw audit trail

Every folder directly under `evidence/` and `evidence/replay/` named by a bare UUID
(`evidence/<run_id>/` for discovery, `evidence/replay/<run_id>/` for replay) is the raw,
unedited output every single run writes on its own — not curated, not all individually
reviewed, kept for completeness and as the literal audit trail the design calls for (every run
gets its own `run_id`-keyed folder with `trace.json`, `log.jsonl`, screenshots, and a result).
Some of these are superseded by, or duplicated in, `by-outcome/` above; several are earlier
debugging runs kept as-is rather than cleaned up. `by-outcome/` is the better starting point for
review — this is here for anyone who wants the full, unfiltered trail.

All evidence in this folder has been grepped for the real service-account password and username
(both absent everywhere) and every screenshot has been visually reviewed before being kept or
referenced here.
