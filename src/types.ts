/**
 * Shared domain types for the Money Operations AP agent.
 *
 * The core transformation in this project converts a VendorInvoice into a
 * ReimbursementForm after the invoice has been extracted and validated.
 */

/** ISO-4217 currency code, e.g. "USD", "EUR", or "GBP". */
export type CurrencyCode = string;

/** ISO-8601 calendar date in YYYY-MM-DD form. */
export type ISODate = string;

/** Monetary amount paired with its currency. */
export interface Money {
  /** Decimal string on the Python API; numbers remain supported for existing UI data. */
  amount: number | string;
  currency: CurrencyCode;
}

/** Postal address associated with a vendor or reimbursement recipient. */
export interface Address {
  line1: string;
  line2?: string;
  city: string;
  stateOrProvince?: string;
  postalCode?: string;
  countryCode: string;
}

/** Vendor identity extracted from an invoice. */
export interface Vendor {
  id?: string;
  name: string;
  normalizedName?: string;
  email?: string;
  taxId?: string;
  address?: Address;
}

/** Individual invoice line extracted from a vendor invoice. */
export interface VendorInvoiceLineItem {
  description: string;
  quantity?: number;
  unitPrice?: Money;
  amount: Money;
  sku?: string;
}

/**
 * Source object produced from an incoming invoice email or attachment.
 */
export interface VendorInvoice {
  id: string;
  invoiceNumber: string;
  vendor: Vendor;
  invoiceDate: ISODate;
  dueDate?: ISODate;
  purchaseOrderNumber?: string;
  subtotal?: Money;
  tax?: Money;
  total: Money;
  lineItems: VendorInvoiceLineItem[];
  memo?: string;
  sourceEmailId?: string;
  attachmentName?: string;
}

/** Review state for a reimbursement form. */
export type ReimbursementStatus =
  | "draft"
  | "ready_for_review"
  | "approved"
  | "rejected";

/**
 * Target object created from a validated VendorInvoice.
 *
 * The transformation should preserve source invoice identifiers so every
 * reimbursement form remains traceable back to the original invoice.
 */
export interface ReimbursementForm {
  id: string;
  sourceVendorInvoiceId: string;
  payeeName: string;
  payeeVendorId?: string;
  invoiceNumber: string;
  purchaseOrderNumber?: string;
  expenseDate: ISODate;
  requestedAmount: Money;
  description: string;
  status: ReimbursementStatus;
  supportingDocumentName?: string;
  sourceEmailId?: string;
  createdAt: string;
}

/** One field-level finding from the CSV validation API. */
export interface InvoiceValidationIssue {
  code: string;
  field: string;
  message: string;
}

/** Per-record response from POST /api/import. */
export interface InvoiceValidationResult {
  row_number: number;
  invoice_number: string;
  session_id: string;
  status: "passed" | "needs_review";
  issues: InvoiceValidationIssue[];
  normalized: Record<string, string>;
  reimbursement: ReimbursementForm | null;
}

/** Batch response, including downloadable reports. */
export interface InvoiceImportResponse {
  results: InvoiceValidationResult[];
  csv: string;
  drafts: ReimbursementForm[];
  tracing_enabled: boolean;
  reference_mode: "configured" | "demo";
}
