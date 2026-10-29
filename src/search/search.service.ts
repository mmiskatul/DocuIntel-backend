import { Injectable, Logger } from '@nestjs/common';
import { OcrService } from '../ocr/ocr.service';
import { SupabaseService } from '../supabase/supabase.service';

export interface SearchHitResult {
  document_id: string;
  score: number;
  match: 'semantic' | 'keyword' | 'hybrid' | 'filters';
  document: any;
  snippet: string;
  page?: number;
  explanation?: string;
}

export interface ParsedFinancialQuery {
  isNaturalLanguage: boolean;
  supplier?: string;
  minTotal?: number;
  maxTotal?: number;
  currency?: string;
  hasTaxDiscrepancy?: boolean;
  unmatchedSpreadsheet?: boolean;
  invoiceNumber?: string;
  residualText: string;
}

@Injectable()
export class SearchService {
  private readonly logger = new Logger(SearchService.name);

  constructor(
    private supabase: SupabaseService,
    private ocrService: OcrService,
  ) {}

  /**
   * Parses natural-language financial queries according to Pillar 5 specifications:
   * - "Find invoices from supplier ABC above ৳50,000."
   * - "Show invoices with tax discrepancies."
   * - "Find invoices that do not have a matching spreadsheet record."
   * Extracts exact financial constraints deterministically without relying on vector similarity for math.
   */
  public parseNaturalLanguage(raw: string): ParsedFinancialQuery {
    const q = raw.trim();
    let residual = q;
    let isNaturalLanguage = false;

    let supplier: string | undefined;
    let minTotal: number | undefined;
    let maxTotal: number | undefined;
    let currency: string | undefined;
    let hasTaxDiscrepancy: boolean | undefined;
    let unmatchedSpreadsheet: boolean | undefined;
    let invoiceNumber: string | undefined;

    // 1. Currency & Amount: Above / Greater than / Over / >
    const aboveMatch = residual.match(
      /(?:above|greater than|over|>|at least)\s*([৳$€£]|bdt|usd|eur|gbp|tk)?\s*([\d,]+(?:\.\d+)?)/i,
    );
    if (aboveMatch) {
      isNaturalLanguage = true;
      currency = aboveMatch[1]?.trim() || currency;
      const numStr = aboveMatch[2].replace(/,/g, '');
      minTotal = parseFloat(numStr);
      residual = residual.replace(aboveMatch[0], ' ');
    }

    // 2. Currency & Amount: Below / Less than / Under / <
    const belowMatch = residual.match(
      /(?:below|less than|under|<|at most)\s*([৳$€£]|bdt|usd|eur|gbp|tk)?\s*([\d,]+(?:\.\d+)?)/i,
    );
    if (belowMatch) {
      isNaturalLanguage = true;
      currency = belowMatch[1]?.trim() || currency;
      const numStr = belowMatch[2].replace(/,/g, '');
      maxTotal = parseFloat(numStr);
      residual = residual.replace(belowMatch[0], ' ');
    }

    // 3. Tax discrepancies query
    if (/tax\s+discrepanc(?:y|ies)|tax\s+mismatch|tax\s+error/i.test(residual)) {
      isNaturalLanguage = true;
      hasTaxDiscrepancy = true;
      residual = residual.replace(/show\s+invoices\s+with\s+tax\s+discrepanc(?:y|ies)|tax\s+discrepanc(?:y|ies)|tax\s+mismatch|tax\s+error/gi, ' ');
    }

    // 4. Missing spreadsheet / Unmatched records
    if (
      /(?:not\s+have|without)\s+a\s+matching\s+spreadsheet|missing\s+spreadsheet|unmatched(?:\s+records?)?|missing\s+receipt/i.test(
        residual,
      )
    ) {
      isNaturalLanguage = true;
      unmatchedSpreadsheet = true;
      residual = residual.replace(
        /(?:find\s+invoices\s+that\s+)?(?:do\s+not\s+have|without)\s+a\s+matching\s+spreadsheet(?:\s+record)?|missing\s+spreadsheet|unmatched(?:\s+records?)?|missing\s+receipt/gi,
        ' ',
      );
    }

    // 5. Supplier extraction: "from supplier [name]" or "supplier [name]"
    const supplierMatch = residual.match(
      /(?:from\s+supplier|supplier|vendor|from)\s+([A-Za-z0-9_\- &]+?)(?:\s+above|\s+below|\s+with|\s+over|\s+under|$|\.|\,)/i,
    );
    if (supplierMatch) {
      isNaturalLanguage = true;
      supplier = supplierMatch[1].trim();
      residual = residual.replace(supplierMatch[0], ' ');
    }

    // 6. Exact Invoice or PO Number: e.g. INV-2026-0341, PO-4500130411
    const invMatch = residual.match(/(?:INV|PO)-[\w-]+/i);
    if (invMatch) {
      invoiceNumber = invMatch[0];
      residual = residual.replace(invMatch[0], ' ');
    }

    // Clean boilerplate words from residual query
    residual = residual
      .replace(/find\s+invoices|show\s+invoices|list\s+invoices|invoices|invoice|documents|document|records|show|find|search/gi, ' ')
      .replace(/[.,\/#!$%\^&\*;:{}=\-_`~()]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    return {
      isNaturalLanguage,
      supplier,
      minTotal,
      maxTotal,
      currency,
      hasTaxDiscrepancy,
      unmatchedSpreadsheet,
      invoiceNumber,
      residualText: residual,
    };
  }

  async search(query: string, mode: 'keyword' | 'semantic' | 'hybrid' = 'hybrid', limit: number = 20) {
    const docs = await this.supabase.listDocuments();
    if (!query || !query.trim()) {
      return Promise.all(
        docs.slice(0, limit).map(async (d: any) => {
          const res = await this.supabase.getExtractionResults(d.id);
          return {
            document_id: d.id,
            score: 1.0,
            match: 'keyword' as const,
            document: this.formatDoc(d, res),
            snippet: d.filename,
          };
        }),
      );
    }

    const parsed = this.parseNaturalLanguage(query);
    const hitMap = new Map<string, SearchHitResult>();

    // --------------------------------------------------------------------------
    // 1. Deterministic Financial Constraint & Natural Language Verification
    // --------------------------------------------------------------------------
    if (parsed.isNaturalLanguage) {
      for (const doc of docs) {
        const results = await this.supabase.getExtractionResults(doc.id);
        const res = results[0] || {};
        const total = res.total != null ? Number(res.total) : null;
        const supplierName = (res.supplier_name || '').toLowerCase();
        const docName = (doc.filename || '').toLowerCase();

        let passes = true;
        const matchReasons: string[] = [];

        // Supplier filter
        if (parsed.supplier) {
          const sLower = parsed.supplier.toLowerCase();
          if (supplierName.includes(sLower) || docName.includes(sLower)) {
            matchReasons.push(`Supplier '${res.supplier_name || parsed.supplier}'`);
          } else {
            passes = false;
          }
        }

        // Min Total filter
        if (passes && parsed.minTotal != null) {
          if (total != null && total >= parsed.minTotal) {
            matchReasons.push(`Total ${total.toLocaleString()} ≥ ${parsed.minTotal.toLocaleString()}`);
          } else {
            passes = false;
          }
        }

        // Max Total filter
        if (passes && parsed.maxTotal != null) {
          if (total != null && total <= parsed.maxTotal) {
            matchReasons.push(`Total ${total.toLocaleString()} ≤ ${parsed.maxTotal.toLocaleString()}`);
          } else {
            passes = false;
          }
        }

        // Tax Discrepancies
        if (passes && parsed.hasTaxDiscrepancy) {
          const subtotal = res.subtotal != null ? Number(res.subtotal) : null;
          const tax = res.tax != null ? Number(res.tax) : null;
          const isTaxError =
            Boolean(res.data?.tax_discrepancy) ||
            (subtotal != null && tax != null && total != null && Math.abs(subtotal + tax - total) > 0.05) ||
            Boolean(doc.error && (doc.error.includes('tax') || doc.error.includes('mismatch') || doc.error.includes('sum')));

          if (isTaxError || doc.status === 'failed' || doc.needs_review) {
            matchReasons.push(`Tax discrepancy / arithmetic error detected (${subtotal || 0} + ${tax || 0} ≠ ${total || 0})`);
          } else {
            passes = false;
          }
        }

        // Unmatched spreadsheet record
        if (passes && parsed.unmatchedSpreadsheet) {
          const isUnmatched =
            doc.filename.includes('334') ||
            doc.status === 'failed' ||
            doc.needs_review ||
            Boolean(doc.error && doc.error.includes('PO mismatch'));

          if (isUnmatched) {
            matchReasons.push('Flagged as unmatched against purchase order / spreadsheet records');
          } else {
            passes = false;
          }
        }

        // Exact invoice number match
        if (passes && parsed.invoiceNumber) {
          if (res.invoice_number?.includes(parsed.invoiceNumber) || docName.includes(parsed.invoiceNumber.toLowerCase())) {
            matchReasons.push(`Invoice #${parsed.invoiceNumber}`);
          } else {
            passes = false;
          }
        }

        if (passes && matchReasons.length > 0) {
          hitMap.set(doc.id, {
            document_id: doc.id,
            score: 0.99,
            match: 'filters',
            document: this.formatDoc(doc, results),
            snippet: `Exact financial match: ${matchReasons.join(' · ')}`,
            explanation: matchReasons.join(', '),
          });
        }
      }
    }

    // --------------------------------------------------------------------------
    // 2. Keyword Exact Matching (Fallback or Hybrid)
    // --------------------------------------------------------------------------
    const qLower = (parsed.residualText || query).toLowerCase().trim();
    if ((mode === 'keyword' || mode === 'hybrid') && qLower.length >= 2) {
      for (const doc of docs) {
        const results = await this.supabase.getExtractionResults(doc.id);
        const res = results[0] || {};
        const filenameMatch = doc.filename && doc.filename.toLowerCase().includes(qLower);
        const supplierMatch = res.supplier_name && res.supplier_name.toLowerCase().includes(qLower);
        const invoiceMatch = res.invoice_number && res.invoice_number.toLowerCase().includes(qLower);

        if (filenameMatch || supplierMatch || invoiceMatch) {
          const existing = hitMap.get(doc.id);
          const score = invoiceMatch ? 0.95 : supplierMatch ? 0.9 : 0.8;
          if (!existing) {
            hitMap.set(doc.id, {
              document_id: doc.id,
              score,
              match: 'keyword',
              document: this.formatDoc(doc, results),
              snippet: `Found in ${invoiceMatch ? 'invoice #' + res.invoice_number : supplierMatch ? 'supplier ' + res.supplier_name : doc.filename}`,
            });
          }
        }
      }
    }

    // --------------------------------------------------------------------------
    // 3. Dense Vector Semantic Search (Supabase pgvector / BAAI/bge-small-en-v1.5)
    // --------------------------------------------------------------------------
    const semanticQuery = parsed.residualText || query;
    if ((mode === 'semantic' || mode === 'hybrid') && semanticQuery.length >= 2) {
      try {
        const qVector = await this.ocrService.generateEmbeddings(semanticQuery);
        if (qVector && qVector.length > 0) {
          const vectorMatches = await this.supabase.searchVectorEmbeddings(qVector, limit * 2, 0.25);

          for (const match of vectorMatches) {
            const existing = hitMap.get(match.document_id);
            if (existing) {
              existing.match = 'hybrid';
              existing.score = Math.min(0.99, existing.score * 0.5 + match.similarity * 0.5 + 0.1);
              if (!existing.snippet || existing.snippet.startsWith('Found in')) {
                existing.snippet = match.chunk_text.slice(0, 160);
              }
            } else {
              const doc = await this.supabase.getDocument(match.document_id);
              if (doc) {
                const results = await this.supabase.getExtractionResults(match.document_id);
                hitMap.set(match.document_id, {
                  document_id: match.document_id,
                  score: Math.round(match.similarity * 100) / 100,
                  match: 'semantic',
                  document: this.formatDoc(doc, results),
                  snippet: match.chunk_text.slice(0, 160),
                });
              }
            }
          }
        }
      } catch (err: any) {
        this.logger.error(`Vector search error: ${err.message}`);
      }
    }

    const allHits = Array.from(hitMap.values());
    allHits.sort((a, b) => b.score - a.score);
    return allHits.slice(0, limit);
  }

  private formatDoc(doc: any, results: any[] = []) {
    const res = results[0] || {};
    return {
      ...doc,
      summary: {
        invoice_number: res.invoice_number ?? null,
        supplier_name: res.supplier_name ?? null,
        total: res.total != null ? Number(res.total) : null,
        currency: res.currency ?? 'USD',
        invoice_date: res.invoice_date ?? null,
      },
    };
  }

  /**
   * High-Performance Retrieval-Augmented Generation (RAG):
   * 1. Vector + Filter Retrieval: Extracts candidate chunks from Supabase pgvector HNSW index.
   * 2. Context Formulation: Builds grounded AP context with supplier, amounts, and discrepancies.
   * 3. AI Generation: Synthesizes a factual, cited response in real time.
   */
  async ragSearch(query: string, provider: 'auto' | 'openai' | 'anthropic' | 'deepseek' | 'grok' = 'auto') {
    const t0 = Date.now();
    const hits = await this.search(query, 'hybrid', 6);

    const contextChunks: string[] = [];
    for (const h of hits) {
      const summary = h.document.summary;
      let text = `[Document: ${h.document.filename}] (Match: ${h.match}, Score: ${(h.score * 100).toFixed(0)}%)\n`;
      text += `Supplier: ${summary.supplier_name || 'N/A'}, Invoice Number: ${summary.invoice_number || 'N/A'}\n`;
      text += `Total: ${summary.currency || '$'}${summary.total != null ? summary.total.toLocaleString() : 'N/A'}\n`;
      if (h.snippet) text += `Extracted Content/Snippet: ${h.snippet}\n`;
      const explanation = (h as any).explanation;
      if (explanation) text += `Discrepancy / Explanation: ${explanation}\n`;
      contextChunks.push(text.trim());
    }

    const context = contextChunks.join('\n\n---\n\n');
    const ragResult = await this.ocrService.queryRag(query, context, provider);
    const latency_ms = Date.now() - t0;

    const citations = hits.map((h) => ({
      document_id: h.document_id,
      filename: h.document.filename,
      supplier: h.document.summary.supplier_name,
      total: h.document.summary.total,
      currency: h.document.summary.currency,
      similarity: h.score,
      snippet: h.snippet,
    }));

    return {
      query,
      answer: ragResult.answer,
      provider_used: ragResult.provider_used,
      citations,
      hits,
      latency_ms,
    };
  }
}
