# AP Agent

An accounts-payable agent that turns invoice emails into bookkeeping records while escalating only the invoices that actually need a human.


## CSV invoice review (working feature)

From the repository root, run:

```bash
python -m src.server
```

Open **http://127.0.0.1:8000** for the live invoice dashboard. Use **Import CSVs**
to select one or more files. The **Import guide & sample** link opens `/import`,
where you can download the sample CSV and validate it. The sample produces **one passed invoice and six review
items**. Download findings as CSV, full results as JSON, or draft reimbursement
forms as JSON. The same live dashboard is also available at `/dashboard`.
The validator and local server use the Python standard library and need no model
API key. No model makes the accounting decisions.

Each row must contain `invoice_number,vendor_id,vendor_name,invoice_date,po_number,total,currency`.
Optional columns: `due_date,subtotal,tax`. Use UTF-8 CSV, ISO dates, and ungrouped
nonnegative decimal amounts with at most two decimal places. Currency must match
the referenced PO. Ambiguous locales are flagged rather than guessed. Arithmetic
is checked when subtotal, tax, and total are all supplied. This version does not
support line-item CSVs, credit notes, or currencies requiring more than two decimals.
Invalid field values produce row-level findings; malformed CSV structure rejects
the import with a clear error. Money in JSON is serialized as decimal strings.

`data/reference.json` contains **fictional demo** vendors, POs, and existing bills.
To use your own trusted reference records, configure their server-side path:

```bash
export BILLIFY_REFERENCE_PATH=/absolute/path/to/reference.json
python -m src.server
```

Use the same JSON shape as the demo. `remaining_amount` must already account for
historical bills. Across imports using the same reference snapshot, passing invoices reserve the remaining PO
balance; every instance of an in-batch duplicate is flagged. Approved aliases are
explicitly listed under each vendor; fuzzy names never establish approval.

### Live dashboard and saved imports

The dashboard reads `GET /api/documents`; it contains no mock invoice records.
Each document is one invoice row, linked to its original CSV filename, row number,
import ID, import time, and PRISM session. Select **All**, **Has errors**, or
**Passed** to filter by actual validation status. Search by invoice, vendor,
filename, or finding; filter by source import; sort by newest, errors first,
passed first, vendor, or invoice number. Selecting a row displays its imported
fields, actual findings, and draft download when available. Export filtered
findings includes only the currently visible invoices. Counts summarize all saved
imports; the list shows the filtered count separately.

`POST /api/import` accepts `{"csv": "...", "filename": "invoices.csv"}`. Each CSV
is stored atomically in `data/billify.sqlite3` (ignored by Git). Set `BILLIFY_DB_PATH`
to change the database path. Imports survive refreshes and server restarts.
Multiple files are processed sequentially; an invalid file shows an error without
rolling back other successful files. Identical filenames are distinguished by
import IDs and timestamps. The dashboard never silently substitutes sample data
when the database is empty or an API request fails.

Passed invoices from earlier imports are included in duplicate checks and their
drafts reserve PO amounts for later imports against the **same reference snapshot**.
Changing reference JSON starts a new validation scope (identified by a SHA-256
fingerprint); old results remain visible as historical decisions. Before changing
the snapshot, reconcile approved/recorded invoices into `existing_bills` and update
PO balances. There is no accounting synchronization or draft release workflow yet.
Reimporting the same CSV creates a new import; previously passed invoices are then
flagged as duplicates rather than creating another draft. Review items do not
reserve a balance and may be corrected and reimported.

Imports made before this persistence feature cannot be recovered from memory;
reimport those CSVs to populate the dashboard. No real payments are made, and
passing invoices remain drafts. The server binds to localhost and has no
production authentication.

### PRISM traces

Install `requirements.txt` and export these variables before starting the server:

```bash
export PRISMTRACE_API_KEY='your-prism-key'
export PRISMTRACE_PROJECT_ID='091ed2f5-9be8-4f04-8ec7-1a174f59ae30'
export PRISMTRACE_HOST='https://prism.blockconvey.com'
python -m src.server
```

No `.env` loader is used: export variables into your shell. Every invoice has its
own session and a root workflow with seven real tool spans: load invoice,
normalize fields, look up vendor, look up PO, search existing bills, validate
amounts, and save decision. SDK callbacks are emitted directly by the deterministic
workflow; these are workflow traces, not claimed LLM calls. Findings and normalized
invoice fields are included in trace outputs. Use fictional data for the demo.
The SDK flushes at each invoice's root-chain completion, so delivery can add
latency. The UI reports whether tracing is configured, not whether remote delivery
succeeded; check the server logs and PRISM Steps to verify receipt.

Correctly flagging a bad invoice is a successful validation run. Execution
exceptions are traced as failures. This feature does not fabricate failed runs,
configure remote alerts, or automatically spend root-cause analysis credits.

### Verification

```bash
python -m unittest discover -s tests -v
node --test tests/test_dashboard.cjs
```

Tests cover financial controls, batch duplicates and balances, mixed valid/invalid
rows, formula-safe CSV export, HTTP upload, and (when the SDK is installed) real
PRISM span serialization against a mocked transport. Live PRISM delivery requires
your project credentials.

## Python model setup and PRISM tracing

The Python model factory in `src/agent.py` configures GPT-6 Astra
(`openai/gpt-6-astra` via the Nicrron OpenAI-compatible API) with
`PRISMtraceCallbackHandler` for project `091ed2f5-9be8-4f04-8ec7-1a174f59ae30`,
under the agent name **Billify AP Agent**. The CSV validation workflow below runs independently of the model factory.
The factory remains available for future model-assisted explanations.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set `NICRRON_API_KEY` and `PRISMTRACE_API_KEY` in your shell environment.
`.env.example` lists the required variables; `.env` files are not loaded
automatically.

Use the factory from the repository root:

```python
from src.agent import create_llm

llm = create_llm()
response = llm.invoke("Explain what information is needed to validate an invoice.")
print(response.content)
```

Invoking the model calls the Nicrron API and sends model traces to PRISM. When adding
a LangChain agent or workflow, also pass the callback through the top-level
invocation's `config={"callbacks": llm.callbacks}` to capture workflow and tool steps;
the model-level callback alone covers model calls.

## Problem

Small finance teams spend hours every week turning invoice emails into bookkeeping records. An invoice lands in a shared inbox, someone reads it, types the vendor, amount, dates, and PO number into QuickBooks, and then should verify that the invoice matches what was ordered and has not already been paid.

That verification step is where money leaks.

Common failure modes include:

- **Duplicate invoices** — the same invoice is re-sent or forwarded and gets paid twice.
- **Overbilling** — the invoice total exceeds the approved purchase order.
- **Unknown or look-alike vendors** — for example, `Acme Corp` versus `ACME Corporation`, a common fraud signal.
- **Missing PO numbers** — the team cannot confirm that the spend was approved.
- **Format traps** — values such as `1.250,00`, foreign currencies, and ambiguous date formats can be interpreted incorrectly.

Most invoices are routine. The hard part is not reading them — it is identifying the small percentage that deserve human attention.

## Solution

AP Agent sits between the invoice inbox and the accounting system.

For every incoming invoice, the agent:

1. Reads the invoice email and attached invoice data.
2. Extracts bookkeeping fields such as vendor, invoice number, amount, currency, dates, and PO number.
3. Normalizes risky formats such as dates, currencies, and locale-specific numbers.
4. Checks the vendor against the approved-vendor list.
5. Checks the invoice against the referenced purchase order.
6. Searches previously logged bills for duplicates or suspicious matches.
7. Decides whether the invoice can be logged automatically or requires human review.
8. Produces a plain-English explanation for any exception.

Routine invoices move through automatically. Suspicious invoices are stopped before money moves.

## Example decisions

| Invoice | Agent decision | Reason |
| --- | --- | --- |
| Known vendor, matching PO, expected amount | **Log bill** | Invoice matches the approved purchase and no duplicate is found. |
| Same invoice number already recorded | **Review** | Possible duplicate payment. |
| Invoice total exceeds PO balance | **Review** | Possible overbilling. |
| Vendor name resembles an approved vendor but is not an exact/approved identity | **Review** | Possible look-alike vendor or fraud attempt. |
| No PO number | **Review** | Spend approval cannot be verified. |
| Ambiguous number/date formatting | **Review or normalize with evidence** | The agent must resolve the format before creating a financial record. |

## Track 2: Money Operations

AI that improves how money moves and how finance teams operate. The track covers payments, expenses, reconciliation, invoices, CFO workflows, and automation.

Billify fits this track by automating a core accounts-payable workflow: transforming incoming vendor invoices into structured bookkeeping records while checking whether each invoice is safe to process automatically.

The agent handles invoice extraction, vendor verification, PO matching, duplicate detection, amount and currency normalization, overbilling checks, and escalation of risky invoices for human review.

### Required PRISM proofs

#### 1. Sessions

Open one run on the PRISM **Steps** tab and show every tool call the agent makes. The workflow is intentionally designed to produce a trace with more than six meaningful steps.

A representative run can include:

1. Read invoice email.
2. Extract invoice fields.
3. Normalize amount, currency, and dates.
4. Resolve vendor identity.
5. Query approved vendors.
6. Query the referenced purchase order.
7. Search existing bills for duplicates.
8. Compare the invoice total against the PO balance.
9. Decide whether to auto-log or flag the invoice.
10. Write the resulting record or review item.

#### 2. Alert

Configure a PRISM alert on a metric that matters to the AP workflow and demonstrate it firing on a real run.

Useful candidates include error rate, latency, vendor-match failure rate, duplicate-detection failures, or unusually high human-review rate. A failed tool call that leaves the run without an answer can be used to demonstrate an error-rate alert.

#### 3. Root Cause

Analyze 5–10 failing runs in PRISM, identify a cluster of at least three failures with the same root cause, then run the same inputs against a corrected v2 and show that they no longer fail.

Good failure clusters for this project include:

- vendor normalization failures such as `Acme Corp` versus `ACME Corporation`,
- ambiguous date parsing such as `03/04/26`,
- locale-sensitive amount parsing such as `1.250,00`,
- duplicate invoices that evade the first matching strategy,
- and missing or malformed PO references.

### Demo: failure, fix, and alert

The PRISM portion of the demo should tell one clean story:

1. **The failure** — show a real invoice run producing an incorrect result or failed tool call.
2. **The alert** — show the monitoring rule that would have surfaced the problem.
3. **The root cause** — show PRISM clustering several related failures and identifying the underlying issue.
4. **The fix** — change the workflow or normalization logic.
5. **The proof** — replay the same inputs and show that v2 succeeds.

## Why this fits Money Operations

Invoice and accounts-payable automation is a direct Money Operations workflow: the agent is making structured decisions about money movement, bookkeeping, approval state, and financial risk.

The important part is not only whether the final decision is correct. Every invoice decision is implemented as a multi-step tool-use chain and traced in **PRISM**. If the agent makes a bad decision, the trace should make it possible to identify:

- what information was extracted,
- which records were queried,
- what comparisons were made,
- where the reasoning or tool output diverged,
- and whether a subsequent fix actually corrected that failure mode.

That makes the AP workflow useful as both a finance automation demo and an agent-observability test case.

## Sponsors

| Sponsor | Logo | Website |
| --- | --- | --- |
| **Block** | <img src="https://www.google.com/s2/favicons?sz=128&domain_url=https://block.xyz" alt="Block logo" width="64" /> | [block.xyz](https://block.xyz) |
| **Convey** | <img src="https://www.google.com/s2/favicons?sz=128&domain_url=https://convey.com" alt="Convey logo" width="64" /> | [convey.com](https://convey.com) |
| **Nicrron.ai** | <img src="https://www.google.com/s2/favicons?sz=128&domain_url=https://nicrron.ai" alt="Nicrron.ai logo" width="64" /> | [nicrron.ai](https://nicrron.ai) |

## Agent workflow

```text
Invoice Inbox
     |
     v
Extract invoice fields
     |
     v
Normalize dates / money / currency
     |
     v
Resolve vendor identity ---------> Approved Vendors
     |
     v
Validate PO ----------------------> Open Purchase Orders
     |
     v
Check prior bills ----------------> Existing Bills
     |
     v
Risk / exception decision
     |
     +----------------------+----------------------+
     |                                             |
     v                                             v
Log bill automatically                       Human review
     |                                             |
     +----------------------+----------------------+
                            v
                       PRISM trace
```

## Data model

At minimum, the agent works with four logical datasets:

### Invoice inbox

Incoming invoice messages and invoice contents.

Typical fields:

- sender
- vendor name
- invoice number
- invoice date
- due date
- subtotal / tax / total
- currency
- PO number

### Approved vendors

The source of truth for entities that are allowed to bill the company.

This is used to catch unknown vendors and suspicious name variations.

### Purchase orders

Approved spend records used to determine whether an invoice was authorized and whether the billed amount is within the remaining PO value.

### Existing bills

Previously recorded bills used for duplicate detection and prior-payment checks.

## Exception categories

The current design focuses on a small set of high-value AP controls:

- `DUPLICATE_INVOICE`
- `PO_OVERBILLING`
- `UNKNOWN_VENDOR`
- `VENDOR_NAME_MISMATCH`
- `MISSING_PO`
- `AMBIGUOUS_AMOUNT_FORMAT`
- `AMBIGUOUS_DATE`
- `UNEXPECTED_CURRENCY`

Each exception should include both a machine-readable category and a plain-English explanation for the finance operator.

## Hackathon scope

This prototype intentionally uses fake integrations so the team can focus on agent behavior, controls, and traceability.

**Included today:**

- Fake invoice inbox backed by JSON data.
- Fake QuickBooks/accounting data backed by JSON files.
- Approved-vendor checks.
- PO validation.
- Duplicate-bill checks.
- Invoice normalization and exception handling.
- Automatic-log versus human-review decisions.
- Plain-English review reasons.
- Multi-step agent traces in PRISM.

**Not included today:**

- Real email credentials or inbox access.
- Real QuickBooks API integration.
- Actual payment execution.
- Production authentication, permissions, or accounting controls.

No real money moves in this demo.

## Demo goal

A strong demo should show both a happy path and failure paths.

For example:

1. Process a routine invoice that matches a known vendor and open PO.
2. Show the agent automatically creating the fake bookkeeping record.
3. Process a duplicate or overbilled invoice.
4. Show the agent refusing to auto-log it and explaining why.
5. Open the PRISM trace and inspect the sequence of extraction, lookup, comparison, and decision steps.
6. Change or fix the failing behavior and replay the case to demonstrate that the trace makes the improvement verifiable.

## Success criteria

The prototype is successful if it can reliably answer two questions for every invoice:

> **Can this invoice be safely logged without a human?**

and, when the answer is no:

> **What exactly does the finance team need to review?**

The end goal is not to replace the finance operator. It is to make routine invoices disappear into automation while concentrating human attention on the invoices where money is actually at risk.
