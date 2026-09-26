# Evidence

**Start here.** The three things the take-home asks for ("a saved example artifact plus logs from both a discovery run and
a replay run; ideally include one replay that hits an error or exceptional state") are the first four links below.
Everything else goes further and is labeled as such.

## The required minimum

1. **Artifact** - [`capabilities/transfer_funds.json`](../capabilities/transfer_funds.json) (repo root, not duplicated
   here; the one file the Replay Engine reads). Its `created_from` names the discovery run below.
2. **Discovery run** - [`by-outcome/discovery/SUCCESS/`](by-outcome/discovery/SUCCESS/): the LLM discovering the whole flow
   from scratch, with `transcript.jsonl` (its own reasoning), `trace.json`, screenshots and `result.json`.
3. **Replay run (happy path)** - [`by-outcome/replay/SUCCESS/`](by-outcome/replay/SUCCESS/): deterministic, `llm_calls: 0`,
   all outputs populated.
4. **Replay runs hitting an exceptional state** -
   [`BUSINESS_OUTCOME_invalid_account`](by-outcome/replay/BUSINESS_OUTCOME_invalid_account/) (a known answer, not a crash) and
   [`HARD_FAILURE_dispatch_unverified`](by-outcome/replay/HARD_FAILURE_dispatch_unverified/) (the irreversible-action
   case: Transfer clicked, confirmation never seen, so the engine stops, never clicks again and opens a ticket).

## Bonus: every other outcome, and the agent path

- [`by-outcome/replay/`](by-outcome/replay/) - 22 replay runs, one folder per scenario, named by outcome; its `README.md`
  is the index. It covers all five outcomes (`SUCCESS`, `BUSINESS_OUTCOME`, `RECOVERABLE` handled by auto-recovery,
  `HARD_FAILURE`, `POLICY_BLOCK`), human escalation (approve, reject, timeout, and a click that disagrees with the typed
  word), decimal amounts, and the same scenarios over HTTP through the agent.
- [`by-outcome/discovery/`](by-outcome/discovery/) - one example per discovery `stop_reason`.
- [`agent_demo/`](agent_demo/) - seven redacted transcripts of the agent-facing HTTP interface.

## How this evidence stays trustworthy

- **Every run has one `run_id`** (a UUID; our own correlation ID, not the bank's). It appears in `trace.json`,
  `result.json`, `log.jsonl` and any ticket, and must be the same everywhere in a folder. Each curated folder's README
  names its source run by that ID.
- **Audit it yourself:** `python scripts/audit_evidence.py --tracked` checks that every run's ID agrees across its files
  and with its README, that each ticket's file name matches its `ticket_id`, that transcripts cite runs present here, and
  that the artifact's `created_from` resolves to a discovery run here. Exit code 0 means clean.
- **Redaction happens at the write boundary:** account numbers and the service-account login never reach these files.
  The CLI's operator block (real values, for a human verifying a ledger) is terminal-only and never written.
- Every folder was grepped for the real service-account username and password (both absent everywhere), and screenshots
  were viewed before being kept. Screenshots mask account numbers and transaction IDs with solid boxes.
