"""Regression tests for accounting decisions and import boundaries."""
import csv
import io
import json
import unittest
from pathlib import Path

from src.invoices import process_csv, read_csv, results_csv

ROOT = Path(__file__).resolve().parents[1]


class InvoiceTests(unittest.TestCase):
    """Exercise controls against independent, explicit expected outcomes."""
    def setUp(self) -> None:
        """Load isolated reference records and a known-valid invoice."""
        self.reference = json.loads((ROOT / 'data/reference.json').read_text())
        self.row = dict(invoice_number='NEW', vendor_id='V001', vendor_name='Acme Supplies',
                        invoice_date='2026-10-01', due_date='2026-10-31', po_number='PO-100',
                        subtotal='100.00', tax='8.00', total='108.00', currency='USD')

    def run_rows(self, *rows: dict[str, str]) -> list[dict]:
        """Serialize test records through the real CSV parser and validator."""
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(self.row))
        writer.writeheader()
        writer.writerows(rows)
        return process_csv(out.getvalue(), self.reference)

    def test_valid_draft_and_exact_decimal(self) -> None:
        """A valid invoice creates a draft with decimal money preserved as text."""
        result = self.run_rows({**self.row, 'subtotal': '0.10', 'tax': '0.20', 'total': '0.30'})[0]
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['reimbursement']['requestedAmount']['amount'], '0.30')
        self.assertEqual(result['reimbursement']['status'], 'draft')

    def test_controls(self) -> None:
        """Every independent bad input is routed to review with a specific reason."""
        cases = [('vendor_id', 'BAD', 'UNKNOWN_VENDOR'), ('vendor_name', 'Acme Scam', 'VENDOR_NAME_MISMATCH'),
                 ('po_number', '', 'MISSING_PO'), ('po_number', 'BAD', 'UNKNOWN_PO'),
                 ('po_number', 'PO-200', 'PO_VENDOR_MISMATCH'), ('currency', 'EUR', 'UNEXPECTED_CURRENCY'),
                 ('invoice_number', 'INV-PAID', 'DUPLICATE_INVOICE'), ('total', '109.00', 'TOTAL_MISMATCH'),
                 ('total', '1001.00', 'PO_OVERBILLING'), ('total', 'NaN', 'INVALID_AMOUNT'),
                 ('total', '1.250,00', 'INVALID_AMOUNT'), ('invoice_date', '03/04/26', 'INVALID_DATE'),
                 ('invoice_date', '2026-02-30', 'INVALID_DATE'), ('due_date', '2026-09-01', 'INVALID_DATE_ORDER')]
        for key, value, code in cases:
            with self.subTest(code=code, value=value):
                result = self.run_rows({**self.row, key: value})[0]
                self.assertIn(code, [i['code'] for i in result['issues']])
                self.assertIsNone(result['reimbursement'])

    def test_all_batch_duplicates_flagged(self) -> None:
        """Neither copy of a duplicated invoice is passed."""
        results = self.run_rows(self.row, {**self.row, 'invoice_number': 'new'})
        self.assertTrue(all(any(i['code'] == 'DUPLICATE_INVOICE' for i in r['issues']) for r in results))

    def test_batch_balance(self) -> None:
        """Multiple individually valid invoices cannot exceed the shared PO balance."""
        first = {**self.row, 'subtotal': '600.00', 'tax': '0', 'total': '600'}
        second = {**first, 'invoice_number': 'NEW2'}
        results = self.run_rows(first, second)
        self.assertEqual(results[0]['status'], 'passed')
        self.assertIn('PO_OVERBILLING', [i['code'] for i in results[1]['issues']])

    def test_bad_row_does_not_abort_batch(self) -> None:
        """A malformed value is reported while subsequent invoices still run."""
        results = self.run_rows({**self.row, 'total': 'oops'}, {**self.row, 'invoice_number': 'NEW2'})
        self.assertEqual([r['status'] for r in results], ['needs_review', 'passed'])

    def test_csv_shape_and_empty(self) -> None:
        """Invalid headers, missing cells and empty files fail explicitly."""
        for content in ['', 'x,x\n1,2', ','.join(self.row), ','.join(self.row) + '\na,b']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_csv(content)

    def test_formula_safe_export(self) -> None:
        """Untrusted invoice identifiers cannot become spreadsheet formulas."""
        results = self.run_rows({**self.row, 'invoice_number': '=1+1'})
        self.assertIn("'=1+1", results_csv(results))

    def test_demo(self) -> None:
        """Demo dataset produces one draft and six reviews."""
        results = process_csv((ROOT / 'data/invoices.csv').read_text(), self.reference)
        self.assertEqual(sum(r['status'] == 'passed' for r in results), 1)
        self.assertEqual(len(results), 7)


if __name__ == '__main__':
    unittest.main()
