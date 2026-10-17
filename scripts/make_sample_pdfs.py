"""Write one PDF invoice per row of data/invoices.csv into data/sample_pdfs/.

    python scripts/make_sample_pdfs.py

No dependencies: a one-page PDF with the standard Helvetica fonts. Values are printed exactly as
they appear in the CSV, so INV-LOCALE keeps its ambiguous date and decimal-comma total.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE_ITEMS = {'Lab Equipment Co': 'Laboratory equipment and calibration'}


def pdf(lines: list[tuple[str, int, int, int, str]]) -> bytes:
    """Lay out (font, size, x, y, text) lines on a US Letter page; F1 regular, F2 bold."""
    def escape(text: str) -> str:
        return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    content = ''.join(f'BT /{font} {size} Tf {x} {y} Td ({escape(text)}) Tj ET\n' for font, size, x, y, text in lines).encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
        b'<< /Length %d >>\nstream\n' % len(content) + content + b'endstream',
    ]
    out, offsets = b'%PDF-1.4\n', []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1) + b''.join(b'%010d 00000 n \n' % o for o in offsets)
    return out + b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)


def invoice(row: dict[str, str]) -> bytes:
    """Print one CSV row as a plain vendor invoice."""
    lines = [('F2', 22, 56, 720, row['vendor_name']), ('F1', 10, 56, 702, 'Accounts receivable'),
             ('F2', 28, 400, 716, 'INVOICE'),
             ('F2', 10, 56, 650, 'Bill to'), ('F1', 10, 56, 636, 'Our Company Inc. - Accounts Payable'),
             ('F1', 10, 56, 622, 'ap@ourcompany.com')]
    details = [('Invoice no.', row['invoice_number']), ('Invoice date', row['invoice_date']), ('Due date', row['due_date'])]
    if row['po_number']:
        details.append(('PO number', row['po_number']))
    for i, (label, value) in enumerate(details):
        lines += [('F2', 10, 380, 650 - 14 * i, label), ('F1', 10, 470, 650 - 14 * i, value)]
    lines += [('F2', 10, 56, 540, 'Description'), ('F2', 10, 470, 540, f"Amount ({row['currency']})"),
              ('F1', 10, 56, 520, LINE_ITEMS.get(row['vendor_name'], 'Office supplies, October order')),
              ('F1', 10, 470, 520, row['subtotal'] or row['total'])]
    totals = [('Subtotal', row['subtotal']), ('Tax', row['tax']), ('Total due', row['total'])]
    y = 480
    for label, value in totals:
        if value:
            font = 'F2' if label == 'Total due' else 'F1'
            lines += [(font, 11, 380, y, label), (font, 11, 470, y, f"{value} {row['currency']}" if label == 'Total due' else value)]
            y -= 18
    lines.append(('F1', 9, 56, 120, f"Please quote {row['invoice_number']} with your payment. Thank you for your business."))
    return pdf(lines)


def main() -> None:
    """Regenerate every sample PDF from the CSV."""
    out = ROOT / 'data/sample_pdfs'
    out.mkdir(exist_ok=True)
    with open(ROOT / 'data/invoices.csv', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        (out / f"{row['invoice_number']}.pdf").write_bytes(invoice(row))
    print(f'wrote {len(rows)} PDFs to {out.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
