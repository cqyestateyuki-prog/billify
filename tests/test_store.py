"""Persistence and cross-import correctness checks."""
import json
import tempfile
import unittest
from pathlib import Path

from src.store import InvoiceStore

ROOT = Path(__file__).resolve().parents[1]


class StoreTests(unittest.TestCase):
    """Check durable results, atomic imports, and historical reservations."""
    def setUp(self) -> None:
        """Create a separate local database for each test."""
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'invoices.sqlite3'
        self.store = InvoiceStore(self.path)
        self.reference = json.loads((ROOT / 'data/reference.json').read_text())
        self.csv = (ROOT / 'data/invoices.csv').read_text()

    def tearDown(self) -> None:
        """Remove the isolated test database."""
        self.directory.cleanup()

    def test_multiple_imports_survive_reopening(self) -> None:
        """All source filenames, findings, and IDs remain after reopening the DB."""
        first = self.store.ingest(self.csv, 'first.csv', self.reference)
        second = self.store.ingest(self.csv, 'second.csv', self.reference)
        documents = InvoiceStore(self.path).documents()
        self.assertEqual(len(documents), 14)
        self.assertEqual({d['filename'] for d in documents}, {'first.csv', 'second.csv'})
        self.assertEqual(len({d['id'] for d in documents}), 14)
        self.assertEqual(sum(d['status'] == 'passed' for d in documents), 1)
        self.assertNotEqual(first['import_id'], second['import_id'])
        self.assertIn('DUPLICATE_INVOICE', [i['code'] for i in second['results'][0]['issues']])

    def test_failed_import_is_atomic(self) -> None:
        """An invalid CSV cannot leave a half-import or erase prior history."""
        self.store.ingest(self.csv, 'good.csv', self.reference)
        with self.assertRaises(ValueError):
            self.store.ingest('bad', 'bad.csv', self.reference)
        self.assertEqual(len(self.store.documents()), 7)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM imports').fetchone()[0], 1)

    def test_reservations_across_files(self) -> None:
        """New invoice numbers still consume the same remaining PO budget."""
        header = self.csv.splitlines()[0]
        row = 'FIRST,V001,Acme Supplies,2026-10-01,2026-10-31,PO-100,600,0,600,USD'
        first = self.store.ingest(header + '\n' + row, 'a.csv', self.reference)
        second = self.store.ingest(header + '\n' + row.replace('FIRST', 'SECOND'), 'b.csv', self.reference)
        self.assertEqual(first['results'][0]['status'], 'passed')
        self.assertIn('PO_OVERBILLING', [i['code'] for i in second['results'][0]['issues']])

    def test_empty_store(self) -> None:
        """A new installation has no fabricated documents."""
        self.assertEqual(self.store.documents(), [])


if __name__ == '__main__':
    unittest.main()
