"""Local CSV review server. Run with python -m src.server."""
from __future__ import annotations

import csv
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.extract import ExtractionError, decode_pdfs, read_pdfs
from src.invoices import results_csv
from src.store import InvoiceStore

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 2_000_000
MAX_PDF_BODY = 24_000_000  # base64 PDFs; Claude accepts up to 32 MB per request


class InvoiceHandler(BaseHTTPRequestHandler):
    """Serve the review UI and a bounded JSON import endpoint on localhost."""
    trace_handler: Any = None

    def store(self) -> InvoiceStore:
        """Resolve the operator-configured persistent invoice database."""
        return InvoiceStore(Path(os.environ.get('BILLIFY_DB_PATH', str(ROOT / 'data/billify.sqlite3'))))

    def respond(self, status: int, body: bytes, content_type: str) -> None:
        """Write a complete response without caching financial data."""
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Serve only explicitly allowed static and demo files."""
        path = urlparse(self.path).path
        if path == '/api/documents':
            try:
                documents = self.store().documents()
                self.respond(200, json.dumps({'documents': documents}).encode(), 'application/json')
            except Exception:
                logging.exception('Could not load invoice history')
                self.respond(500, b'{"error":"Could not load invoice history. See server logs."}', 'application/json')
            return
        files = {'/': ('ui/dashboard.html', 'text/html; charset=utf-8'),
                 '/import': ('ui/import.html', 'text/html; charset=utf-8'),
                 '/dashboard': ('ui/dashboard.html', 'text/html; charset=utf-8'),
                 '/sample.csv': ('data/invoices.csv', 'text/csv; charset=utf-8'),
                 '/dashboard.js': ('ui/dashboard.js', 'text/javascript; charset=utf-8'),
                 '/document-filters.js': ('ui/document-filters.js', 'text/javascript; charset=utf-8')}
        if path not in files:
            self.respond(404, b'Not found', 'text/plain')
            return
        filename, content_type = files[path]
        self.respond(200, (ROOT / filename).read_bytes(), content_type)

    def do_POST(self) -> None:
        """Validate uploaded CSV or invoice PDFs using server-owned reference data."""
        if self.path not in ('/api/import', '/api/import-pdf'):
            self.respond(404, b'Not found', 'text/plain')
            return
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            self.respond(403, b'Cross-origin imports are not allowed', 'text/plain')
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = MAX_PDF_BODY if self.path == '/api/import-pdf' else MAX_BODY
            if size <= 0 or size > limit:
                raise ValueError(f'Upload must be between 1 byte and {limit // 1_000_000} MB.')
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Expected application/json.')
            payload = json.loads(self.rfile.read(size))
            reference_path = Path(os.environ.get('BILLIFY_REFERENCE_PATH', str(ROOT / 'data/reference.json')))
            reference = json.loads(reference_path.read_text(encoding='utf-8'))
            if self.path == '/api/import-pdf':
                batch = self.import_pdfs(payload, reference)
            else:
                if not isinstance(payload, dict) or not isinstance(payload.get('csv'), str):
                    raise ValueError('Provide a csv string.')
                filename = payload.get('filename', 'uploaded.csv')
                if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
                    raise ValueError('Filename must be a nonempty string of at most 255 characters.')
                batch = self.store().ingest(payload['csv'], filename, reference, self.trace_handler)
            results = batch['results']
            body = {**batch, 'csv': results_csv(results),
                    'drafts': [r['reimbursement'] for r in results if r['reimbursement']],
                    'tracing_enabled': self.trace_handler is not None,
                    'reference_mode': 'configured' if os.environ.get('BILLIFY_REFERENCE_PATH') else 'demo'}
            self.respond(200, json.dumps(body).encode(), 'application/json')
        except ExtractionError as exc:
            self.respond(exc.status, json.dumps({'error': str(exc)}).encode(), 'application/json')
        except (ValueError, UnicodeError, csv.Error) as exc:
            self.respond(400, json.dumps({'error': str(exc)}).encode(), 'application/json')
        except Exception:
            logging.exception('Invoice import failed')
            self.respond(500, b'{"error":"Import failed. See server logs for the underlying error."}', 'application/json')


    def import_pdfs(self, payload: Any, reference: dict[str, Any]) -> dict[str, Any]:
        """Read PDFs with Claude, then save each invoice as its own import named after its file."""
        invoices, skipped = read_pdfs(decode_pdfs(payload), reference, self.trace_handler)
        if not invoices:
            raise ValueError('No invoices could be read: ' + '; '.join(s['reason'] for s in skipped))
        store, imports, results = self.store(), [], []
        for invoice in invoices:
            saved = store.ingest(invoice['csv'], invoice['file'], reference, self.trace_handler,
                                 [invoice['session_id']], [{'pdf_read': invoice['pdf_read']}])
            imports.append({k: saved[k] for k in ('import_id', 'filename', 'created_at')})
            results.extend(saved['results'])
        return {'imports': imports, 'results': results, 'skipped': skipped}


def main() -> None:
    """Start a local demo service and flush PRISM traces at shutdown."""
    logging.basicConfig(level=logging.INFO)
    if os.environ.get('PRISMTRACE_API_KEY'):
        from prismtrace import PRISMtraceCallbackHandler
        InvoiceHandler.trace_handler = PRISMtraceCallbackHandler(
            api_key=os.environ['PRISMTRACE_API_KEY'],
            project_id=os.environ.get('PRISMTRACE_PROJECT_ID', '091ed2f5-9be8-4f04-8ec7-1a174f59ae30'),
            host=os.environ.get('PRISMTRACE_HOST', 'https://prism.blockconvey.com'),
            agent_name='Billify CSV Validator',
        )
    port = int(os.environ.get('PORT', '8000'))
    server = HTTPServer(('127.0.0.1', port), InvoiceHandler)
    logging.info('Billify CSV review: http://127.0.0.1:%s (PRISM enabled: %s)', port, bool(InvoiceHandler.trace_handler))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if InvoiceHandler.trace_handler:
            InvoiceHandler.trace_handler.close()


if __name__ == '__main__':
    main()
