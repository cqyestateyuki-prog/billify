"""Read invoice PDFs with Claude and hand the fields to the CSV validator."""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import PurePath
from typing import Any, Callable
from uuid import uuid4

from src.invoices import WorkflowTrace

MODEL = os.environ.get('BILLIFY_EXTRACT_MODEL', 'claude-opus-5-5')
COLUMNS = ['invoice_number', 'vendor_id', 'vendor_name', 'invoice_date', 'due_date', 'po_number', 'subtotal', 'tax', 'total', 'currency']
FIELDS = [c for c in COLUMNS if c != 'vendor_id']
WRITTEN = ['invoice_date', 'due_date', 'subtotal', 'tax', 'total', 'currency']
MAX_FILES = 20
MAX_PDF_BYTES = 8_000_000

PROMPT = """Read this vendor invoice and fill in the fields.

- invoice_number, vendor_name and po_number exactly as printed. vendor_name is the company sending the bill, not the customer.
- invoice_date and due_date as YYYY-MM-DD. If a date can be read two ways (03/04/26 with nothing on the page that says which part is the day), leave it empty and say why in ambiguities.
- subtotal, tax and total as plain decimals with a dot and two places (1250.00). Convert 1.250,00 or 1,250.00 only when the page makes the format clear; otherwise leave it empty and say why.
- currency as a three-letter ISO code (USD, EUR). If only a symbol is printed, give the code and note the assumption in ambiguities.
- as_written: the dates, amounts and currency exactly as printed, so a reviewer can check every conversion.
- Use an empty string for anything that is not on the invoice. Never make a value up.
- is_invoice is false when the document is not an invoice."""

_text = {'type': 'string'}
SCHEMA = {
    'type': 'object',
    'properties': {
        'is_invoice': {'type': 'boolean'},
        **{name: _text for name in FIELDS},
        'as_written': {'type': 'object', 'properties': {name: _text for name in WRITTEN},
                       'required': WRITTEN, 'additionalProperties': False},
        'ambiguities': {'type': 'array', 'items': _text},
    },
    'required': ['is_invoice', *FIELDS, 'as_written', 'ambiguities'],
    'additionalProperties': False,
}


class ExtractionError(Exception):
    """Reading could not run at all (credentials, network); the batch stops."""
    def __init__(self, message: str, status: int = 502) -> None:
        """Keep the HTTP status the server should answer with."""
        super().__init__(message)
        self.status = status


def decode_pdfs(payload: Any) -> list[tuple[str, bytes]]:
    """Accept {"files": [{"name", "data" (base64)}]} and keep only real, bounded PDFs."""
    files = payload.get('files') if isinstance(payload, dict) else None
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError(f'Provide 1 to {MAX_FILES} PDF files.')
    decoded = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('data'), str):
            raise ValueError('Each file needs a name and base64 data.')
        name = PurePath(item['name']).name[:120] or 'invoice.pdf'
        try:
            data = base64.b64decode(item['data'], validate=True)
        except binascii.Error as exc:
            raise ValueError(f'{name} is not valid base64.') from exc
        if not data.startswith(b'%PDF-'):
            raise ValueError(f'{name} is not a PDF.')
        if len(data) > MAX_PDF_BYTES:
            raise ValueError(f'{name} is larger than {MAX_PDF_BYTES // 1_000_000} MB.')
        decoded.append((name, data))
    return decoded


def read_pdf(data: bytes, filename: str) -> dict[str, Any]:
    """Ask Claude for one invoice's fields; ValueError means this file can't be used."""
    import anthropic
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            # Server-side fallback: a safety decline is retried on Anthropic's recommended model.
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            output_config={'effort': 'medium', 'format': {'type': 'json_schema', 'schema': SCHEMA}},
            messages=[{'role': 'user', 'content': [
                {'type': 'document', 'title': filename,
                 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': base64.standard_b64encode(data).decode()}},
                {'type': 'text', 'text': PROMPT},
            ]}],
        )
    except (anthropic.CredentialsError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ExtractionError('Claude credentials are missing or not allowed: set ANTHROPIC_API_KEY on the server.', 503) from exc
    except TypeError as exc:
        # With no credentials at all, the SDK raises TypeError("Could not resolve authentication method ...").
        if 'authentication' not in str(exc):
            raise
        raise ExtractionError('Claude credentials are missing: set ANTHROPIC_API_KEY on the server.', 503) from exc
    except anthropic.BadRequestError as exc:
        raise ValueError(f'{filename}: Claude could not read this PDF ({exc.message}).') from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f'Claude returned {exc.status_code} while reading {filename}; try again.') from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError('Could not reach the Claude API; check the network and try again.') from exc
    if response.stop_reason == 'refusal':
        raise ValueError(f'{filename}: Claude declined to read this document.')
    if response.stop_reason == 'max_tokens':
        raise ValueError(f'{filename}: the reply was cut off; try again.')
    text = next((block.text for block in response.content if block.type == 'text'), '')
    try:
        fields = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'{filename}: Claude returned unreadable output.') from exc
    fields['model'] = response.model
    return fields


def match_vendor(fields: dict[str, Any], reference: dict[str, Any]) -> tuple[str, str]:
    """Find the vendor ID by approved name or alias, else by the PO's vendor; never by similarity.

    Falling back to the PO's vendor keeps look-alike names visible: the validator then
    reports VENDOR_NAME_MISMATCH instead of a bare missing ID.
    """
    name = fields.get('vendor_name', '').strip().casefold()
    for vendor in reference['vendors']:
        if name and name in {n.casefold() for n in [vendor['name'], *vendor.get('aliases', [])]}:
            return vendor['id'], 'name'
    po = next((p for p in reference['purchase_orders'] if p['number'] == fields.get('po_number', '').strip()), None)
    if po:
        return po['vendor_id'], 'purchase_order'
    return '', 'none'


def to_csv(rows: list[dict[str, str]]) -> str:
    """Write rows in the validator's column order."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def read_pdfs(files: list[tuple[str, bytes]], reference: dict[str, Any], handler: Any = None,
              read: Callable[[bytes, str], dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read every PDF in parallel; return one validator-ready CSV per invoice, plus the files skipped.

    Each read opens a PRISM session that the validation run continues (pass session_id to ingest).
    """
    read = read or read_pdf

    def extract(item: tuple[str, bytes]) -> tuple[str, str, dict[str, Any] | None, str]:
        """Read one file inside its own PRISM session."""
        name, data = item
        session_id = str(uuid4())
        if handler:
            from prismtrace import session
            context = session(session_id)
        else:
            context = nullcontext()
        try:
            with context, WorkflowTrace(handler, 'read_invoice_pdf') as trace:
                return name, session_id, trace.step('extract_invoice_fields', lambda: read(data, name)), ''
        except ValueError as exc:
            return name, session_id, None, str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(extract, files))
    invoices, skipped = [], []
    for name, session_id, fields, problem in reads:
        if fields is None or not fields.get('is_invoice'):
            skipped.append({'file': name, 'reason': problem or 'This document is not an invoice.'})
            continue
        vendor_id, matched_by = match_vendor(fields, reference)
        values = {key: str(fields.get(key, '')).strip() for key in FIELDS}
        invoices.append({'file': name, 'session_id': session_id, 'csv': to_csv([{**values, 'vendor_id': vendor_id}]),
                         'pdf_read': {'file': name, 'fields': values, 'vendor_id': vendor_id, 'vendor_matched_by': matched_by,
                                      'as_written': fields.get('as_written', {}),
                                      'ambiguities': fields.get('ambiguities', []), 'model': fields.get('model')}})
    return invoices, skipped
