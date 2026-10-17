"""PDF reading, vendor matching and the /api/import-pdf endpoint, without calling Claude."""
import base64
import csv
import json
import tempfile
import threading
import unittest
from collections import defaultdict
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from src import extract
from src.extract import FIELDS, WRITTEN, ExtractionError, decode_pdfs, match_vendor, read_pdf, read_pdfs
from src.server import InvoiceHandler, ROOT
from src.store import InvoiceStore

REFERENCE = json.loads((ROOT / 'data/reference.json').read_text())
ROWS = list(csv.DictReader((ROOT / 'data/invoices.csv').open(encoding='utf-8')))
PDFS = [(f"{row['invoice_number']}.pdf", (ROOT / f"data/sample_pdfs/{row['invoice_number']}.pdf").read_bytes()) for row in ROWS]


def fake_read(data: bytes, name: str) -> dict:
    """Answer like Claude would for the sample PDFs: clear values converted, the ambiguous date left empty."""
    row = next(r for r in ROWS if f"{r['invoice_number']}.pdf" == name)
    fields = {key: row[key] for key in FIELDS}
    fields.update(is_invoice=True, as_written={key: row[key] for key in WRITTEN}, ambiguities=[], model='fake')
    if row['invoice_number'] == 'INV-LOCALE':
        fields.update(invoice_date='', total='1250.00',
                      ambiguities=['Invoice date 03/04/26 could be 3 April or 4 March 2026.'])
    return fields


def fake_client(response=None, error=None) -> SimpleNamespace:
    """Stand in for anthropic.Anthropic(), recording the request it receives."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return response
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)), calls=calls)


class ExtractTests(unittest.TestCase):
    """Turn PDFs into validator rows without trusting anything the file doesn't say."""
    def test_decode_pdfs(self) -> None:
        """Accept real PDFs; reject other files, bad base64 and oversized batches with a clear message."""
        name, data = PDFS[0]
        files = decode_pdfs({'files': [{'name': '../../' + name, 'data': base64.b64encode(data).decode()}]})
        self.assertEqual(files, [(name, data)])
        for payload, message in [({'files': []}, '1 to 20'),
                                 ({'files': [{'name': 'a.pdf', 'data': base64.b64encode(b'hello').decode()}]}, 'not a PDF'),
                                 ({'files': [{'name': 'a.pdf', 'data': '%%%'}]}, 'not valid base64'),
                                 ({'csv': 'x'}, '1 to 20')]:
            with self.assertRaisesRegex(ValueError, message):
                decode_pdfs(payload)

    def test_match_vendor(self) -> None:
        """Match approved names and aliases; fall back to the PO's vendor so look-alikes still get flagged."""
        self.assertEqual(match_vendor({'vendor_name': 'acme supplies llc', 'po_number': ''}, REFERENCE), ('V001', 'name'))
        self.assertEqual(match_vendor({'vendor_name': 'Acme Supply Partners', 'po_number': 'PO-100'}, REFERENCE), ('V001', 'purchase_order'))
        self.assertEqual(match_vendor({'vendor_name': 'Unknown LLC', 'po_number': 'PO-999'}, REFERENCE), ('', 'none'))

    def test_read_pdf_request_and_failures(self) -> None:
        """Send the PDF as a document with a schema and fallback; map refusals and missing credentials."""
        import anthropic
        answer = json.dumps(fake_read(b'', 'INV-001.pdf') | {'model': None})
        ok = SimpleNamespace(stop_reason='end_turn', model='claude-opus-5-5', content=[SimpleNamespace(type='text', text=answer)])
        client = fake_client(ok)
        with patch('anthropic.Anthropic', return_value=client):
            fields = read_pdf(PDFS[0][1], 'INV-001.pdf')
        self.assertEqual(fields['invoice_number'], 'INV-001')
        request = client.calls[0]
        self.assertEqual(request['model'], extract.MODEL)
        self.assertEqual((request['betas'], request['fallbacks']), (['server-side-fallback-2026-07-01'], 'default'))
        self.assertEqual(request['output_config']['format']['schema'], extract.SCHEMA)
        document = request['messages'][0]['content'][0]
        self.assertEqual((document['type'], document['source']['media_type']), ('document', 'application/pdf'))
        refused = SimpleNamespace(stop_reason='refusal', model='claude-opus-5-5', content=[])
        with patch('anthropic.Anthropic', return_value=fake_client(refused)), self.assertRaisesRegex(ValueError, 'declined'):
            read_pdf(PDFS[0][1], 'INV-001.pdf')
        with patch('anthropic.Anthropic', return_value=fake_client(error=anthropic.CredentialsError('no credentials'))):
            with self.assertRaises(ExtractionError) as error:
                read_pdf(PDFS[0][1], 'INV-001.pdf')
        self.assertEqual(error.exception.status, 503)
        no_auth = TypeError('Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set.')
        with patch('anthropic.Anthropic', return_value=fake_client(error=no_auth)), self.assertRaises(ExtractionError) as error:
            read_pdf(PDFS[0][1], 'INV-001.pdf')
        self.assertEqual(error.exception.status, 503)

    def ingest_all(self, store: InvoiceStore, invoices: list[dict], handler=None) -> list[dict]:
        """Save each read invoice the way the server does: one import per PDF."""
        return [store.ingest(i['csv'], i['file'], REFERENCE, handler, [i['session_id']], [{'pdf_read': i['pdf_read']}])['results'][0]
                for i in invoices]

    def test_pdfs_reach_csv_decisions(self) -> None:
        """The seven sample PDFs reach the same decisions as the sample CSV, plus the date ambiguity."""
        invoices, skipped = read_pdfs(PDFS, REFERENCE, read=fake_read)
        self.assertEqual(skipped, [])
        with tempfile.TemporaryDirectory() as folder:
            results = self.ingest_all(InvoiceStore(Path(folder) / 'test.sqlite3'), invoices)
        decisions = {r['invoice_number']: [i['code'] for i in r['issues']] for r in results}
        self.assertEqual(decisions['INV-001'], [])
        self.assertIn('PO_OVERBILLING', decisions['INV-OVER'])
        self.assertIn('VENDOR_NAME_MISMATCH', decisions['INV-LOOK'])
        self.assertIn('MISSING_FIELD', decisions['INV-LOCALE'])
        self.assertIn('MISSING_PO', decisions['INV-NOPO'])
        self.assertEqual(sum(r['status'] == 'passed' for r in results), 1)
        self.assertEqual([r['pdf_read']['file'] for r in results], [name for name, _ in PDFS])
        self.assertEqual([r['session_id'] for r in results], [i['session_id'] for i in invoices])

    def test_unreadable_and_non_invoice_files_are_skipped(self) -> None:
        """Keep going when one file fails, and say which file and why."""
        def read(data: bytes, name: str) -> dict:
            if name == 'INV-001.pdf':
                raise ValueError('INV-001.pdf: Claude declined to read this document.')
            if name == 'INV-OVER.pdf':
                return {'is_invoice': False}
            return fake_read(data, name)
        invoices, skipped = read_pdfs(PDFS, REFERENCE, read=read)
        self.assertEqual(len(invoices), 5)
        self.assertEqual([(s['file'], 'declined' in s['reason']) for s in skipped], [('INV-001.pdf', True), ('INV-OVER.pdf', False)])

    def test_endpoint(self) -> None:
        """Save one import per PDF, list them with what was read; 503 without Claude, 400 when nothing reads."""
        temporary = tempfile.TemporaryDirectory()
        environment = patch.dict('os.environ', {'BILLIFY_DB_PATH': str(Path(temporary.name) / 'test.sqlite3')})
        environment.start()
        server = HTTPServer(('127.0.0.1', 0), InvoiceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        body = json.dumps({'files': [{'name': n, 'data': base64.b64encode(d).decode()} for n, d in PDFS]}).encode()
        post = lambda: urlopen(Request(base + '/api/import-pdf', body, {'Content-Type': 'application/json'}))
        try:
            with patch('src.extract.read_pdf', fake_read), post() as response:
                result = json.load(response)
            self.assertEqual((len(result['results']), len(result['drafts']), len(result['imports'])), (7, 1, 7))
            self.assertIn('VENDOR_NAME_MISMATCH', result['csv'])
            with urlopen(base + '/api/documents') as response:
                documents = json.load(response)['documents']
            self.assertEqual(sorted(d['filename'] for d in documents), sorted(n for n, _ in PDFS))
            locale = next(d for d in documents if d['invoice_number'] == 'INV-LOCALE')
            self.assertIn('03/04/26', locale['pdf_read']['ambiguities'][0])
            def unavailable(data: bytes, name: str) -> dict:
                raise ExtractionError('Claude credentials are missing: set ANTHROPIC_API_KEY on the server.', 503)
            with patch('src.extract.read_pdf', unavailable), self.assertRaises(HTTPError) as error:
                post()
            self.assertEqual(error.exception.code, 503)
            self.assertIn('ANTHROPIC_API_KEY', json.load(error.exception)['error'])
            def declined(data: bytes, name: str) -> dict:
                raise ValueError(f'{name}: Claude declined to read this document.')
            with patch('src.extract.read_pdf', declined), self.assertRaises(HTTPError) as error:
                post()
            self.assertEqual(error.exception.code, 400)
            self.assertIn('No invoices could be read', json.load(error.exception)['error'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            environment.stop()
            temporary.cleanup()

    def test_prism_sessions_include_pdf_reading(self) -> None:
        """Each PDF invoice is one PRISM session: a reading run (1 tool) then the validation run (7 tools)."""
        try:
            import httpx
            from prismtrace import PRISMtraceCallbackHandler
        except ImportError:
            self.skipTest('Install requirements.txt to test the PRISM SDK contract.')
        payloads = []

        def capture(request: 'httpx.Request') -> 'httpx.Response':
            """Capture the SDK HTTP payload without accessing a live account."""
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={'ok': True})

        with patch('prismtrace.langchain_handler._HAS_LANGCHAIN', True), patch.dict('os.environ', {}, clear=True):
            handler = PRISMtraceCallbackHandler(api_key='test-key', project_id='test-project')
        handler._client.close()
        handler._client = httpx.Client(transport=httpx.MockTransport(capture))
        try:
            with tempfile.TemporaryDirectory() as folder:
                invoices, _ = read_pdfs(PDFS, REFERENCE, handler, read=fake_read)
                results = self.ingest_all(InvoiceStore(Path(folder) / 'test.sqlite3'), invoices, handler)
        finally:
            handler.close()
        tools = defaultdict(list)
        for payload in payloads:
            tools[payload['session_id']].append(sum(s['span_type'] == 'tool' for s in payload['spans']))
        self.assertEqual(set(tools), {r['session_id'] for r in results})
        self.assertTrue(all(sorted(counts) == [1, 7] for counts in tools.values()), dict(tools))


if __name__ == '__main__':
    unittest.main()
