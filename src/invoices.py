"""Deterministic CSV invoice validation and draft reimbursement generation."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from types import TracebackType
from uuid import uuid4

REQUIRED = {'invoice_number', 'vendor_id', 'vendor_name', 'invoice_date', 'po_number', 'total', 'currency'}


@dataclass
class Issue:
    """One actionable validation finding tied to a source field."""
    code: str
    field: str
    message: str


@dataclass
class InvoiceResult:
    """An invoice decision with source data and an optional draft form."""
    row_number: int
    invoice_number: str
    session_id: str
    status: str = 'needs_review'
    issues: list[Issue] = field(default_factory=list)
    normalized: dict[str, str] = field(default_factory=dict)
    reimbursement: dict[str, Any] | None = None


class WorkflowTrace:
    """Emit actual validation tool spans to an optional PRISM callback handler."""
    def __init__(self, handler: Any = None) -> None:
        """Retain a shared callback handler for all invoices in an import."""
        self.handler = handler
        self.run_id = uuid4()

    def __enter__(self) -> WorkflowTrace:
        """Open one root run so all seven tool steps share a trajectory."""
        if self.handler:
            self.handler.on_chain_start({'name': 'validate_invoice'}, {}, run_id=self.run_id)
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """Close and flush the run, recording real execution failures as errors."""
        if self.handler:
            if error:
                self.handler.on_chain_error(error, run_id=self.run_id)
            else:
                self.handler.on_chain_end({'completed': True}, run_id=self.run_id)

    def step(self, name: str, operation: Callable[[], Any]) -> Any:
        """Execute a step and preserve exceptions in the trace and caller."""
        run_id = uuid4()
        if self.handler:
            self.handler.on_tool_start({'name': name, 'description': name}, '{}', run_id=run_id, parent_run_id=self.run_id)
        try:
            result = operation()
        except Exception as exc:
            if self.handler:
                self.handler.on_tool_error(exc, run_id=run_id)
            raise
        if self.handler:
            self.handler.on_tool_end(str(result), run_id=run_id)
        return result


def money(value: str) -> Decimal:
    """Parse unambiguous decimal notation, rejecting separators and nonfinite values."""
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        raise ValueError('Use a nonnegative decimal with at most two fractional digits, e.g. 1250.00.')
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid amount.') from exc


def read_csv(content: str) -> list[dict[str, str]]:
    """Read UTF-8 CSV with strict headers, retaining each logical record's line number."""
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')), strict=True)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)):
        raise ValueError('Duplicate CSV headers are not allowed.')
    missing = REQUIRED - set(headers)
    if missing:
        raise ValueError('Missing CSV columns: ' + ', '.join(sorted(missing)))
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f'CSV record ending at line {reader.line_num} has the wrong number of fields.')
        rows.append({**{key: value.strip() for key, value in row.items()}, '_row': str(reader.line_num)})
        if len(rows) > 5000:
            raise ValueError('Limit imports to 5,000 invoices.')
    if not rows:
        raise ValueError('CSV contains no invoices.')
    return rows


def validate_reference(reference: dict[str, Any]) -> None:
    """Reject unusable reference data rather than treating missing records as approval."""
    for key in ('vendors', 'purchase_orders', 'existing_bills'):
        if not isinstance(reference.get(key), list):
            raise ValueError(f'Reference data requires a {key} list.')
    for vendor in reference['vendors']:
        if not vendor.get('id') or not vendor.get('name'):
            raise ValueError('Each vendor needs id and name.')
    for po in reference['purchase_orders']:
        for key in ('number', 'vendor_id', 'currency', 'remaining_amount'):
            if not po.get(key):
                raise ValueError(f'Each PO needs {key}.')
        money(str(po['remaining_amount']))
    for bill in reference['existing_bills']:
        if not bill.get('vendor_id') or not bill.get('invoice_number'):
            raise ValueError('Each existing bill needs vendor_id and invoice_number.')
    for key, identity in (('vendors', 'id'), ('purchase_orders', 'number')):
        ids = [item[identity] for item in reference[key]]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Duplicate identities in {key}.')


def process_csv(content: str, reference: dict[str, Any], handler: Any = None) -> list[dict[str, Any]]:
    """Validate a batch against trusted references; reserve PO balances within the batch."""
    from contextlib import nullcontext
    validate_reference(reference)
    rows = read_csv(content)
    vendors = {v['id']: v for v in reference['vendors']}
    orders = {p['number']: p for p in reference['purchase_orders']}
    balances = {p['number']: money(str(p['remaining_amount'])) for p in orders.values()}
    counts = Counter((r['vendor_id'], r['invoice_number'].casefold()) for r in rows)
    existing = {(b['vendor_id'], b['invoice_number'].strip().casefold()) for b in reference['existing_bills']}
    results = []
    for row in rows:
        result = InvoiceResult(int(row['_row']), row['invoice_number'], str(uuid4()))
        trace = WorkflowTrace(handler)
        if handler:
            from prismtrace import session
            context = session(result.session_id)
        else:
            context = nullcontext()
        with context, trace:
            trace.step('load_invoice', lambda: {'row': result.row_number, 'invoice_number': result.invoice_number})
            amounts: dict[str, Decimal] = {}

            def flag(code: str, field_name: str, message: str) -> None:
                """Append a review finding without aborting the batch."""
                result.issues.append(Issue(code, field_name, message))

            def normalize() -> dict[str, str]:
                """Validate fields without guessing locale or inventing missing values."""
                for key in REQUIRED:
                    if not row[key]:
                        flag('MISSING_PO' if key == 'po_number' else 'MISSING_FIELD', key, f'{key} is required.')
                result.normalized = {k: v for k, v in row.items() if k != '_row'}
                for key in ('subtotal', 'tax', 'total'):
                    if row.get(key):
                        try:
                            amounts[key] = money(row[key])
                            result.normalized[key] = format(amounts[key], '.2f')
                        except ValueError as exc:
                            flag('INVALID_AMOUNT', key, str(exc))
                for key in ('invoice_date', 'due_date'):
                    if row.get(key):
                        try:
                            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row[key]):
                                raise ValueError()
                            date.fromisoformat(row[key])
                        except ValueError:
                            flag('INVALID_DATE', key, 'Use a valid ISO date: YYYY-MM-DD.')
                if row.get('due_date') and not any(i.code == 'INVALID_DATE' for i in result.issues):
                    if row['invoice_date'] and row['due_date'] < row['invoice_date']:
                        flag('INVALID_DATE_ORDER', 'due_date', 'Due date precedes invoice date.')
                if not re.fullmatch('[A-Z]{3}', row['currency']):
                    flag('INVALID_CURRENCY', 'currency', 'Use an uppercase three-letter currency code.')
                return result.normalized

            trace.step('normalize_fields', normalize)

            def check_vendor() -> dict[str, Any]:
                """Match stable identity and explicit approved aliases."""
                vendor = vendors.get(row['vendor_id'])
                if not vendor:
                    flag('UNKNOWN_VENDOR', 'vendor_id', 'Vendor ID is not in the approved vendor list.')
                elif row['vendor_name'].casefold() not in {n.casefold() for n in [vendor['name'], *vendor.get('aliases', [])]}:
                    flag('VENDOR_NAME_MISMATCH', 'vendor_name', 'Name does not match this vendor or an approved alias.')
                return {'matched': vendor is not None}

            trace.step('lookup_vendor', check_vendor)
            po = orders.get(row['po_number'])

            def check_po() -> dict[str, Any]:
                """Verify PO existence, ownership, and currency."""
                if row['po_number'] and not po:
                    flag('UNKNOWN_PO', 'po_number', 'Purchase order was not found.')
                if po:
                    if po['vendor_id'] != row['vendor_id']:
                        flag('PO_VENDOR_MISMATCH', 'po_number', 'PO belongs to another vendor.')
                    if po['currency'] != row['currency']:
                        flag('UNEXPECTED_CURRENCY', 'currency', 'Currency differs from the purchase order.')
                return {'matched': po is not None}

            trace.step('lookup_purchase_order', check_po)

            def duplicates() -> dict[str, bool]:
                """Flag every occurrence of a duplicate, including earlier batch rows."""
                key = (row['vendor_id'], row['invoice_number'].casefold())
                duplicate = counts[key] > 1 or key in existing
                if duplicate:
                    flag('DUPLICATE_INVOICE', 'invoice_number', 'Invoice appears in this batch more than once or is already recorded.')
                return {'duplicate': duplicate}

            trace.step('search_existing_bills', duplicates)

            def compare_amounts() -> dict[str, str]:
                """Check exact arithmetic and the remaining batch-adjusted PO balance."""
                if all(k in amounts for k in ('subtotal', 'tax', 'total')):
                    if amounts['subtotal'] + amounts['tax'] != amounts['total']:
                        flag('TOTAL_MISMATCH', 'total', 'Subtotal plus tax does not equal total.')
                if 'total' in amounts:
                    if amounts['total'] == 0:
                        flag('INVALID_AMOUNT', 'total', 'Invoice total must be greater than zero.')
                    if po and amounts['total'] > balances[po['number']]:
                        flag('PO_OVERBILLING', 'total', f"Total exceeds remaining PO balance of {balances[po['number']]:.2f} {po['currency']}.")
                return {'findings': str(len(result.issues))}

            trace.step('validate_amounts', compare_amounts)

            def save_decision() -> dict[str, Any]:
                """Build a draft only after every check passes; never approve payment."""
                if not result.issues:
                    result.status = 'passed'
                    balances[row['po_number']] -= amounts['total']
                    result.reimbursement = {
                        'id': str(uuid4()), 'sourceVendorInvoiceId': result.session_id,
                        'payeeName': row['vendor_name'], 'payeeVendorId': row['vendor_id'],
                        'invoiceNumber': row['invoice_number'], 'purchaseOrderNumber': row['po_number'],
                        'expenseDate': row['invoice_date'],
                        'requestedAmount': {'amount': format(amounts['total'], '.2f'), 'currency': row['currency']},
                        'description': f"Invoice {row['invoice_number']}", 'status': 'draft',
                        'createdAt': datetime.now(timezone.utc).isoformat(),
                    }
                return asdict(result)

            results.append(trace.step('save_decision', save_decision))
    return results


def results_csv(results: list[dict[str, Any]]) -> str:
    """Export decisions with spreadsheet formula injection protection."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['row_number', 'invoice_number', 'status', 'issue_codes', 'explanation', 'session_id'])
    for result in results:
        values = [result['row_number'], result['invoice_number'], result['status'],
                  '; '.join(i['code'] for i in result['issues']),
                  '; '.join(i['message'] for i in result['issues']), result['session_id']]
        writer.writerow(["'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
    return output.getvalue()
