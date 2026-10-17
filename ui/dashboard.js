/* Render stored invoice evidence without inserting untrusted HTML. */
const state = { documents: [], selected: null, status: 'all', visible: [] };
const $ = selector => document.querySelector(selector);
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function chip(doc) { return element('span', doc.status === 'passed' ? 'Passed' : `${doc.issues.length} finding${doc.issues.length === 1 ? '' : 's'}`, `chip ${doc.status === 'passed' ? 'pass' : 'error'}`); }
function download(text, type, filename) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = element('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function showDetail(doc) {
  const target = $('#detail'); target.replaceChildren();
  if (!doc) { target.append(element('div', state.documents.length ? 'No invoices match these filters.' : 'No imported invoices yet. Import a CSV to get started.', 'card empty')); return; }
  const header = element('article', undefined, 'card');
  header.append(chip(doc), element('h2', doc.invoice_number || '(Missing invoice number)'), element('p', doc.normalized.vendor_name || '(Missing vendor)', 'muted'), element('p', `${doc.filename} · CSV line ${doc.row_number} · ${new Date(doc.created_at).toLocaleString()}`, 'muted'));
  target.append(header);
  const findings = element('article', undefined, `card ${doc.issues.length ? 'findings' : ''}`);
  findings.append(element('h3', doc.issues.length ? 'Needs review' : 'All configured checks passed'));
  if (!doc.issues.length) findings.append(element('p', 'A draft reimbursement form is available. No payment has been authorized.'));
  for (const issue of doc.issues) {
    const item = element('div', undefined, 'finding');
    item.append(element('strong', issue.code), element('p', issue.message), element('small', `Field: ${issue.field}`, 'muted')); findings.append(item);
  }
  target.append(findings);
  const split = element('div', undefined, 'split');
  const fields = element('article', undefined, 'card'); fields.append(element('h3', 'Imported invoice fields'));
  const dl = element('dl');
  for (const [key, value] of Object.entries(doc.normalized)) dl.append(element('dt', key.replaceAll('_', ' ')), element('dd', value || '—'));
  fields.append(dl); split.append(fields);
  const provenance = element('article', undefined, 'card'); provenance.append(element('h3', 'Source & decision'));
  const meta = element('dl');
  for (const [key, value] of [['Source CSV', doc.filename], ['CSV line', doc.row_number], ['Import ID', doc.import_id], ['PRISM session', doc.session_id], ['Decision', doc.status], ['Form state', doc.reimbursement ? doc.reimbursement.status : 'Not created']]) meta.append(element('dt', key), element('dd', value));
  provenance.append(meta);
  if (doc.reimbursement) {
    const btn = element('button', 'Download draft form', 'export');
    btn.addEventListener('click', () => download(JSON.stringify(doc.reimbursement, null, 2), 'application/json', 'billify-draft.json')); provenance.append(btn);
  }
  split.append(provenance); target.append(split);
}
function render() {
  const query = $('#search').value.trim().toLowerCase(), batch = $('#batch').value;
  const docs = filterDocuments(state.documents, {status: state.status, query, batch, sort: $('#sort').value});
  state.visible = docs;
  if (!docs.some(doc => doc.id === state.selected)) state.selected = docs[0]?.id || null;
  const list = $('#list'); list.replaceChildren();
  $('#visible-count').textContent = `${docs.length} of ${state.documents.length} invoices`;
  $('#export').disabled = docs.length === 0;
  for (const doc of docs) {
    const button = element('button', undefined, 'doc'); button.dataset.id = doc.id; button.dataset.status = doc.status;
    button.setAttribute('aria-pressed', String(doc.id === state.selected));
    const top = element('div', undefined, 'top'); top.append(element('span', doc.normalized.vendor_name || '(Missing vendor)'), element('span', `${doc.normalized.total || '—'} ${doc.normalized.currency || ''}`));
    button.append(top, element('strong', doc.invoice_number || '(Missing invoice number)'), chip(doc), element('small', `${doc.filename} · line ${doc.row_number}`));
    button.addEventListener('click', () => { state.selected = doc.id; render(); }); list.append(button);
  }
  if (!docs.length) list.append(element('p', state.documents.length ? 'No matching invoices.' : 'No imports yet.', 'empty'));
  showDetail(docs.find(doc => doc.id === state.selected));
}
async function load() {
  const response = await fetch('/api/documents', { cache: 'no-store' });
  const data = await response.json(); if (!response.ok) throw Error(data.error || 'Could not load invoices.');
  state.documents = data.documents;
  $('#total').textContent = state.documents.length;
  $('#passed').textContent = state.documents.filter(d => d.status === 'passed').length;
  $('#errors').textContent = state.documents.filter(d => d.status === 'needs_review').length;
  const batches = new Map(state.documents.map(d => [d.import_id, d])); $('#batches').textContent = batches.size;
  const prior = $('#batch').value; $('#batch').replaceChildren(new Option('All CSV imports', ''));
  for (const [id, doc] of batches) $('#batch').append(new Option(`${doc.filename} · ${new Date(doc.created_at).toLocaleString()} · ${id.slice(0, 8)}`, id));
  if (batches.has(prior)) $('#batch').value = prior;
  render();
}
for (const button of document.querySelectorAll('[data-status]')) button.addEventListener('click', () => {
  state.status = button.dataset.status;
  for (const tab of document.querySelectorAll('.tabs button')) tab.setAttribute('aria-pressed', String(tab === button));
  render();
});
$('#search').addEventListener('input', render); $('#batch').addEventListener('change', render); $('#sort').addEventListener('change', render);
$('#refresh').addEventListener('click', () => load().then(() => { $('#feedback').textContent = 'Invoice history refreshed.'; }).catch(error => { $('#feedback').textContent = error.message; }));
$('#upload').addEventListener('click', () => $('#files').click());
$('#files').addEventListener('change', async () => {
  const files = [...$('#files').files]; if (!files.length) return;
  $('#upload').disabled = true; let completed = 0; const failures = [];
  for (const file of files) {
    $('#feedback').textContent = `Importing ${file.name} (${completed + failures.length + 1}/${files.length})…`;
    try {
      if (file.size > 1900000) throw Error('CSV exceeds 1.9 MB.');
      const csv = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      const response = await fetch('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ csv, filename: file.name }) });
      const data = await response.json(); if (!response.ok) throw Error(data.error || 'Import failed.');
      completed++;
    } catch (error) { failures.push(`${file.name}: ${error.message}`); }
  }
  try { await load(); } catch (error) { failures.push(error.message); }
  $('#feedback').textContent = `${completed} CSV import${completed === 1 ? '' : 's'} saved.${failures.length ? ' Errors: ' + failures.join(' | ') : ''}`;
  $('#files').value = ''; $('#upload').disabled = false;
});
$('#export').addEventListener('click', () => {
  const safe = value => { let text = String(value ?? ''); if (/^[\s]*[=+@-]/.test(text)) text = "'" + text; return '"' + text.replaceAll('"', '""') + '"'; };
  const rows = [['filename', 'import_id', 'row_number', 'invoice_number', 'status', 'issue_codes', 'explanation', 'session_id'], ...state.visible.map(d => [d.filename, d.import_id, d.row_number, d.invoice_number, d.status, d.issues.map(i => i.code).join('; '), d.issues.map(i => i.message).join('; '), d.session_id])];
  download(rows.map(row => row.map(safe).join(',')).join('\r\n'), 'text/csv;charset=utf-8', 'billify-filtered-findings.csv');
});
load().catch(error => { $('#feedback').textContent = error.message; });
