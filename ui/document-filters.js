/** Select and order actual imported records without changing stored order. */
function filterDocuments(documents, {status = 'all', query = '', batch = '', sort = 'newest'} = {}) {
  const docs = documents.filter(doc => (status === 'all' || status === doc.status) && (!batch || doc.import_id === batch) && (!query || [doc.invoice_number, doc.filename, doc.normalized.vendor_name, ...doc.issues.flatMap(i => [i.code, i.message])].join(' ').toLowerCase().includes(query)));
  docs.sort((a, b) => {
    if (sort === 'errors' || sort === 'passed') {
      const desired = sort === 'errors' ? 'needs_review' : 'passed';
      const order = Number(b.status === desired) - Number(a.status === desired); if (order) return order;
    }
    if (sort === 'vendor') return (a.normalized.vendor_name || '').localeCompare(b.normalized.vendor_name || '');
    if (sort === 'invoice') return a.invoice_number.localeCompare(b.invoice_number, undefined, { numeric: true });
    return b.created_at.localeCompare(a.created_at) || a.row_number - b.row_number;
  });
  return docs;
}

if (typeof module !== 'undefined') module.exports = { filterDocuments };
