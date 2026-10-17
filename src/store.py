"""SQLite persistence for imported CSVs and their invoice validation results."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.invoices import process_csv


class InvoiceStore:
    """Persist complete imports atomically and reserve draft amounts across imports."""
    def __init__(self, path: Path) -> None:
        """Create the local database if it does not already exist."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
                    reference_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES imports(id),
                    result TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS documents_import ON documents(import_id);
            ''')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a database connection with named rows and foreign keys enabled."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ingest(self, content: str, filename: str, reference: dict[str, Any], handler: Any = None) -> dict[str, Any]:
        """Validate and save one CSV as a transaction against the reference snapshot."""
        reference_hash = hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()
        adjusted = json.loads(json.dumps(reference))
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('''SELECT d.result FROM documents d
                JOIN imports i ON d.import_id = i.id WHERE i.reference_hash = ?''', (reference_hash,)).fetchall()
            reserved: dict[str, Decimal] = {}
            for row in previous:
                result = json.loads(row['result'])
                if result['status'] != 'passed':
                    continue
                invoice = result['normalized']
                adjusted['existing_bills'].append({'vendor_id': invoice['vendor_id'], 'invoice_number': invoice['invoice_number']})
                po = invoice['po_number']
                reserved[po] = reserved.get(po, Decimal('0')) + Decimal(invoice['total'])
            for po in adjusted['purchase_orders']:
                po['remaining_amount'] = str(max(Decimal('0'), Decimal(str(po['remaining_amount'])) - reserved.get(po['number'], Decimal('0'))))
            results = process_csv(content, adjusted, handler)
            import_id = str(uuid4())
            created_at = datetime.now(timezone.utc).isoformat()
            connection.execute('INSERT INTO imports VALUES (?, ?, ?, ?)', (import_id, filename, created_at, reference_hash))
            for result in results:
                connection.execute('INSERT INTO documents VALUES (?, ?, ?)', (result['session_id'], import_id, json.dumps(result)))
        return {'import_id': import_id, 'filename': filename, 'created_at': created_at, 'results': results}

    def documents(self) -> list[dict[str, Any]]:
        """Return actual stored invoices, newest imports first, preserving row order."""
        with self.connect() as connection:
            rows = connection.execute('''SELECT d.id, d.result, i.id AS import_id,
                i.filename, i.created_at FROM documents d JOIN imports i ON i.id = d.import_id
                ORDER BY i.created_at DESC, d.rowid ASC''').fetchall()
        return [{**json.loads(row['result']), 'id': row['id'], 'import_id': row['import_id'],
                 'filename': row['filename'], 'created_at': row['created_at']} for row in rows]
