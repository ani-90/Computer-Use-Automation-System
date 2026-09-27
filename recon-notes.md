# Recon notes (Phase 0)

Manual observations of the self-hosted ParaBank (`parasoft/parabank`, `http://localhost:8080/parabank`).
Account numbers are deliberately not recorded here; they are demo parameters, never hardcoded.

## Environment
- Container `parabank` restarted with `docker start` (not recreated). `/parabank/index.htm` returns 200.
- The `agentdemo` login and its accounts survived the restart.
- Two accounts exist: account A (source, $1100.00) and account B (overdrawn, -$584.50, available $0.00).
- The total across accounts is $515.50. Transfers move money between accounts; they do not remove it.

## Accounts Overview
- One table with columns `Account`, `Balance*`, `Available Amount`, plus a `Total` row.
- Balances render as text with the dollar sign attached: `$1100.00`. No thousands separator.
- Negative balances render as `-$584.50`.
- The `Total` row shares the table, so balance extraction must target the specific account's row.

## Transfer Funds
- Fields: `Amount` (text input), `From account #` and `to account #` (dropdowns), `TRANSFER` button.
- Both dropdowns default to the same first account, so the flow must select a different destination.
- The dropdowns only list real accounts, so a nonexistent account cannot be submitted through the UI.
  The `invalid_account` outcome is reached through the replay parameter: `option[value={{to_account}}]`
  is absent from the populated dropdown.

## Find Transactions and Transaction Details
- Find Transactions offers search by transaction ID, date, date range and amount, for a selected account.
- Searching by amount returns a table (`Date`, `Transaction`, `Debit (-)`, `Credit (+)`). The transaction
  description is a link; the ID is not in the table.
- The link opens Transaction Details: `Transaction ID`, `Date`, `Description`, `Type`, `Amount`.
- So a bank-side transaction ID is reachable in about three steps after the transfer. Matching by amount
  is best-effort if the same amount was transferred more than once.
- Ordering, checked with two identical $5 transfers on the same day: results list oldest-first. The earlier
  transfer is the top row, so the newest match is the last row. The rows look identical in the table; the
  ID is only visible after opening a row.

## Sidebar
- The sidebar has an `Admin Page` link (ParaBank admin controls, including database initialization).
  The Policy Gate must deny it.

## ParaBank behaviour confirmed manually
- No overdraft protection: an account can go negative (account B is evidence of this).
- **Sub-cent amounts corrupt the account (found by hand, 2026-09-26).** Transferring `1.98484` was accepted:
  the confirmation showed `$1.98`, but the unrounded value left the account with a balance of five decimal
  places. Afterwards every page that formats that balance failed with an internal error, permanently
  (container log: `java.lang.ArithmeticException: Rounding necessary`; a restart does not clear it because
  the balance is stored data — only recreating the container did). Target validation cannot be assumed, so
  the capability contract enforces money precision (at most 2 decimal places) before anything is dispatched.
- ParaBank prints money with exactly two decimals in its confirmation (`$5.00`, `$1.44`), so any artifact
  that hardcodes the app's `.00` after the amount can only confirm whole-number transfers.

## Adapter findings (Phase 2 smoke test)
- Login: the Username and Password inputs have no accessible name. The label is a plain paragraph just
  before each input. The adapter locates them by role plus position (nth) and shows the label as a hint.
- Repeated names: header and footer links repeat, and the Find Transactions page has four buttons all named
  `Find Transactions`. These need `nth` to resolve.
- After login, `Transfer Funds` in the sidebar is a single link. It appears twice on the home page.
- Account numbers are 5 digits. Screenshot masking boxes only the number (checked on Accounts Overview).
- Pages visited so far: index, overview, transfer, findtrans. All are on the allowlist. `transaction.htm`
  has not been visited by the script yet.
- Accounts Overview balances are plain table text, not interactive elements, so they are not in the
  candidate list.

## Open items
- Locator viability on the transfer and Find Transactions pages (names with special characters, duplicate
  buttons): to be confirmed by the Phase 2 rerun.
- Real `error_mapping` content for `BUSINESS_OUTCOME`: Phase 6 probe run.

## Container reset (2026-09-26)

The sub-cent corruption above (transferring `1.98484`) left the container permanently unable to format one
account's balance. Per that finding, a `docker start` doesn't clear it — only recreating the container does,
since the corrupted value is stored data, not process state. The container was recreated
(`docker rm -f parabank`, then `docker run` again), which — unlike the `docker start` restart in "Environment"
above — does **not** preserve data: `agentdemo` and its accounts did not survive this one, and the login was
re-registered from scratch (profile name set to "Test Agent"; account numbers, again, not recorded here).

**Everything in "Environment" above (the `$1100.00`/`-$584.50`/`$515.50` balances, and the "survived the
restart" line) describes the pre-corruption instance and is no longer current.** The behavioral findings
elsewhere in this file (no overdraft protection, the sub-cent corruption itself, oldest-first transaction
ordering, the fixed two-decimal confirmation format) are properties of the app, not of one instance's data, and
remain accurate. Every discovery run and replay scenario now committed under `evidence/` was run against the
post-reset instance.
