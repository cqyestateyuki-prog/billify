/** Exercise combined dashboard filters and sort order without browser dependencies. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {filterDocuments} = require('../ui/document-filters.js');
const documents = [
  {id:'1',status:'passed',import_id:'a',filename:'first.csv',invoice_number:'INV-2',normalized:{vendor_name:'Acme'},issues:[],created_at:'2026-10-17T10:00:00Z',row_number:2},
  {id:'2',status:'needs_review',import_id:'a',filename:'first.csv',invoice_number:'INV-10',normalized:{vendor_name:'Zen'},issues:[{code:'PO_OVERBILLING',message:'Exceeds PO'}],created_at:'2026-10-17T10:00:00Z',row_number:3},
  {id:'3',status:'needs_review',import_id:'b',filename:'second.csv',invoice_number:'INV-3',normalized:{vendor_name:'Acme'},issues:[{code:'DUPLICATE_INVOICE',message:'Already recorded'}],created_at:'2026-10-17T11:00:00Z',row_number:2}
];
const ids = options => filterDocuments(documents, options).map(d=>d.id);
test('actual statuses distinguish passed and review invoices',()=>{
  assert.deepEqual(ids({status:'passed'}),['1']);
  assert.deepEqual(ids({status:'needs_review'}),['3','2']);
});
test('source, error search, and status filters compose',()=>{
  assert.deepEqual(ids({batch:'a',status:'needs_review',query:'overbilling'}),['2']);
  assert.deepEqual(ids({batch:'a',status:'passed',query:'overbilling'}),[]);
  assert.deepEqual(ids({query:'second.csv'}),['3']);
});
test('error-first and passed-first ordering retains source data',()=>{
  assert.deepEqual(ids({sort:'errors'}),['3','2','1']);
  assert.deepEqual(ids({sort:'passed'}),['1','3','2']);
  assert.deepEqual(ids({sort:'invoice'}),['1','3','2']);
  assert.deepEqual(documents.map(d=>d.id),['1','2','3']);
});
test('empty data remains empty',()=>assert.deepEqual(filterDocuments([]),[]));
