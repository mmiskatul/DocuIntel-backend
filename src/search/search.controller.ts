import { Controller, Get, Query, Res } from '@nestjs/common';
import { Response } from 'express';
import { SearchService } from './search.service';

@Controller('api')
export class SearchController {
  constructor(private readonly searchService: SearchService) {}

  @Get('search')
  async search(
    @Query('q') query: string = '',
    @Query('mode') mode: 'keyword' | 'semantic' | 'hybrid' = 'hybrid',
    @Query('limit') limit: number = 20,
  ) {
    return this.searchService.search(query, mode, Number(limit) || 20);
  }

  @Get('search/export')
  async exportSearchCsv(
    @Query('q') query: string = '',
    @Query('mode') mode: 'keyword' | 'semantic' | 'hybrid' = 'hybrid',
    @Res() res: Response,
  ) {
    const hits = await this.searchService.search(query, mode, 100);

    const headers = [
      'Document ID',
      'Filename',
      'Status',
      'Supplier',
      'Invoice Number',
      'Date',
      'Total',
      'Currency',
      'Match Type',
      'Score',
      'Evidence / Snippet',
    ];

    const rows = hits.map((h: any) => [
      `"${h.document_id}"`,
      `"${(h.document?.filename || '').replace(/"/g, '""')}"`,
      `"${h.document?.status || ''}"`,
      `"${(h.document?.summary?.supplier_name || '').replace(/"/g, '""')}"`,
      `"${(h.document?.summary?.invoice_number || '').replace(/"/g, '""')}"`,
      `"${h.document?.summary?.invoice_date || ''}"`,
      h.document?.summary?.total ?? '',
      `"${h.document?.summary?.currency || 'USD'}"`,
      `"${h.match}"`,
      h.score,
      `"${(h.snippet || '').replace(/"/g, '""')}"`,
    ]);

    const csvContent = [headers.join(','), ...rows.map((r: any) => r.join(','))].join('\r\n');

    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="search_export_${Date.now()}.csv"`,
    );
    return res.send(csvContent);
  }
}

