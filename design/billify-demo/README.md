# Billify · demo with the PRISM proofs

`billify.html` is the Billify demo you picked (`reference/Billify (Copy).html`, untouched copy), kept as it was (white theme, front door, Add → Review → Transfer to the simulated Acme Expense portal), with the three PRISM proofs from the README added on top. Open it straight from disk; it loads two Google Fonts and nothing else.

## Demo script (about 90 seconds)

1. **Demo settings → tick "Send values exactly as written (v1)".** The header chip turns amber: `agent v1`.
2. **Try it with sample invoices → Yes, read with Billify → Transfer 7 invoices.** 1 created, 6 rejected by the portal.
3. **Alert:** the red card says the PRISM alert fired: 6 of 7 runs failed at `submit_expense` (86%), rule "failed runs > 20% of the last 10".
4. **Session:** click any invoice in the left list. The drawer shows its 9 tool calls; in v1 the four cleanup steps say "Skipped" and `submit_expense` ends in `422 · INVALID_DATE_FORMAT, UNSUPPORTED_CURRENCY`.
5. **Root cause:** "Why they failed" groups the 6 failures by cause (dates 6, currency 5, amount 3, category 3) and names the shared root cause: v1 sends values as written.
6. **Fix and proof:** "Replay the same 7 invoices with v2" re-reads the same inputs with cleanup on and sends them to a fresh draft report: 7 created, and the banner reads `agent v1: 6 rejected → agent v2: 0 rejected`. Open the same invoice again: the cleanup steps now show `10/03/2026 → 2026-10-03`, `$ → USD`.

## What changed from the reference

| Change | Why |
|---|---|
| Light theme fixed (`data-theme="light"`) | The original follows the system setting, so it turned dark on dark-mode machines. User ruling: the demo is white everywhere. The dark tokens are still in the file. |
| `agent v1 / v2` chip in the header; PRISM note in Demo settings | The audience can always see which version is running. |
| 3 more sample invoices (Pixel Print, Lumen Electric, Kraftwerk Teile) → 7 | PRISM Root Cause needs 5–10 failing runs with 3+ sharing a cause; 4 samples gave 3 failures. |
| v2 cleanup now handles `15.10.2026` and `1.250,00` | Before, v2 read the German amount as `1.250` (one euro twenty-five) and couldn't parse the date. |
| Category rules for utilities and equipment | So the new samples map to portal categories in v2. |
| **Agent steps drawer** (from Review cards and Transfer rows) | PRISM Sessions: 9 tool calls per invoice, each with its result. |
| **PRISM alert card** after a failing transfer | PRISM Alert: shows the rule and that it fired on this run. |
| **Why they failed** + **Replay with v2** | PRISM Root Cause: cluster, named cause, same inputs on v2, proof. |

Not changed: layout, copy, colours, fonts, the portal and its validation rules.

## What has to be real for PRISM

Everything on this page runs in the browser, so **nothing reaches PRISM yet**. For the proofs to be real, the Python agent (`src/agent.py`, already wired to `PRISMtraceCallbackHandler`) must run each invoice as these tool calls, in this order, and return their results to the page:

`read_document` → `extract_fields` → `normalize_date` → `normalize_amount` → `normalize_currency` → `map_category` → `validate_fields` → `fill_portal_form` → `submit_expense`

- v1 = the four `normalize_*` / `map_category` tools return the input unchanged; v2 = they clean it up.
- A 422 from the portal should make `submit_expense` a failed tool call, so the PRISM error-rate alert can fire.
- The page needs back, per invoice: each step's output line, the PRISM session URL, and the alert URL (the "Open … in PRISM" buttons show a placeholder until then).

## Files

- `billify.html` — the demo
- `reference/Billify (Copy).html` — the file it started from
- `capture.js` — walks the whole story and saves `screens/` (desktop + phone); exits 1 on a JS error. Uses the Playwright install in `~/Desktop/tools/ui-diff` and local Chrome; change the `require` path to run it elsewhere.
- `screens/` — 01 home … 09 steps after the v2 replay, plus `phone-*`
