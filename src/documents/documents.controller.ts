import {
  Body,
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  NotFoundException,
  Param,
  Patch,
  Post,
  Query,
  Res,
  UploadedFiles,
  UseInterceptors,
} from '@nestjs/common';
import { FilesInterceptor } from '@nestjs/platform-express';
import { Response } from 'express';
import { DocumentsService } from './documents.service';
import { CrossVerifyDto } from './dto/cross-verify.dto';

@Controller('api')
export class DocumentsController {
  constructor(private readonly documentsService: DocumentsService) {}

  @Post('documents')
  @HttpCode(HttpStatus.ACCEPTED)
  @UseInterceptors(FilesInterceptor('files', 50))
  async uploadDocuments(@UploadedFiles() files: Express.Multer.File[]) {
    if (!files || files.length === 0) {
      return [];
    }
    return this.documentsService.uploadAndProcess(files);
  }

  @Get('documents')
  async listDocuments(@Query() query: any) {
    return this.documentsService.listDocuments(query);
  }

  @Get('documents/:id')
  async getDocument(@Param('id') id: string) {
    return this.documentsService.getDocument(id);
  }

  @Get('documents/:id/results')
  async getResults(@Param('id') id: string) {
    return this.documentsService.getResults(id);
  }

  @Get('documents/:id/events')
  async getEvents(@Param('id') id: string) {
    return this.documentsService.getHistory(id);
  }

  @Get('documents/:id/history')
  async getHistory(@Param('id') id: string) {
    return this.documentsService.getHistory(id);
  }

  @Get('documents/:id/file')
  async getFile(@Param('id') id: string, @Res() res: Response) {
    const file = this.documentsService.getFile(id);
    if (!file) {
      // Return a clean inline SVG placeholder if document buffer not in memory
      res.setHeader('Content-Type', 'image/svg+xml');
      return res.send(`
        <svg xmlns="http://www.w3.org/2000/svg" width="600" height="850" viewBox="0 0 600 850" style="background:#f8fafc; font-family:sans-serif;">
          <rect width="100%" height="100%" fill="#ffffff" stroke="#e2e8f0" stroke-width="2"/>
          <text x="50" y="80" font-size="24" font-weight="bold" fill="#1e293b">NEXORA SOLUTIONS LLC</text>
          <text x="50" y="110" font-size="12" fill="#64748b">123 Innovation Way, Suite 400 • Austin, TX 78701</text>
          <text x="420" y="80" font-size="28" font-weight="bold" fill="#0f172a">INVOICE</text>
          <rect x="410" y="95" width="140" height="30" fill="#fef9c3" stroke="#eab308" stroke-width="1.5" rx="4"/>
          <text x="420" y="115" font-size="13" font-weight="bold" fill="#854d0e">INV-2026-0417</text>
          <line x1="50" y1="160" x2="550" y2="160" stroke="#cbd5e1" stroke-width="1.5"/>
          <rect x="50" y="240" width="500" height="260" fill="#f8fafc" stroke="#e2e8f0" rx="6"/>
          <text x="70" y="270" font-size="13" font-weight="bold" fill="#334155">DESCRIPTION</text>
          <text x="320" y="270" font-size="13" font-weight="bold" fill="#334155">QTY</text>
          <text x="380" y="270" font-size="13" font-weight="bold" fill="#334155">UNIT</text>
          <text x="480" y="270" font-size="13" font-weight="bold" fill="#334155">AMOUNT</text>
          <line x1="50" y1="285" x2="550" y2="285" stroke="#cbd5e1"/>
          <text x="70" y="320" font-size="13" fill="#1e293b">Cloud Infrastructure Upgrade</text>
          <text x="325" y="320" font-size="13" fill="#1e293b">2</text>
          <text x="380" y="320" font-size="13" fill="#1e293b">$5,000.00</text>
          <text x="475" y="320" font-size="13" font-weight="bold" fill="#1e293b">$10,000.00</text>
          <text x="70" y="360" font-size="13" fill="#1e293b">Data Integration &amp; ETL</text>
          <text x="325" y="360" font-size="13" fill="#1e293b">1</text>
          <text x="380" y="360" font-size="13" fill="#1e293b">$2,400.00</text>
          <text x="475" y="360" font-size="13" font-weight="bold" fill="#1e293b">$2,400.00</text>
          <text x="70" y="400" font-size="13" fill="#1e293b">Security Assessment</text>
          <text x="325" y="400" font-size="13" fill="#1e293b">1</text>
          <text x="380" y="400" font-size="13" fill="#1e293b">$1,500.00</text>
          <text x="475" y="400" font-size="13" font-weight="bold" fill="#1e293b">$1,500.00</text>
          <rect x="360" y="520" width="190" height="120" fill="#f1f5f9" stroke="#cbd5e1" rx="6"/>
          <text x="380" y="550" font-size="13" fill="#475569">Subtotal:</text>
          <text x="480" y="550" font-size="13" font-weight="bold" fill="#1e293b">$13,900.00</text>
          <text x="380" y="580" font-size="13" fill="#475569">Tax (8.25%):</text>
          <text x="485" y="580" font-size="13" font-weight="bold" fill="#1e293b">$1,146.75</text>
          <rect x="370" y="600" width="170" height="30" fill="#fef08a" stroke="#ca8a04" stroke-width="1.5" rx="4"/>
          <text x="380" y="620" font-size="13" font-weight="bold" fill="#854d0e">Total Due:</text>
          <text x="465" y="620" font-size="14" font-weight="bold" fill="#854d0e">$15,046.75</text>
        </svg>
      `);
    }
    res.setHeader('Content-Type', file.mimetype);
    res.setHeader('Content-Disposition', `inline; filename="${file.filename}"`);
    return res.send(file.buffer);
  }

  @Patch('results/:id')
  async correctResult(
    @Param('id') id: string,
    @Body() body: { corrections: any; reviewer?: string; reason?: string },
  ) {
    return this.documentsService.updateResult(
      parseInt(id, 10),
      body.corrections,
      body.reviewer,
      body.reason,
    );
  }

  @Post('documents/:id/approve')
  async approveDocument(
    @Param('id') id: string,
    @Body() body: { reviewer?: string },
  ) {
    return this.documentsService.approveDocument(id, body?.reviewer);
  }

  @Post('documents/batch-approve')
  async batchApprove(@Body() body: { document_ids: string[]; reviewer?: string }) {
    return this.documentsService.batchApprove(body?.document_ids || [], body?.reviewer);
  }

  @Post('documents/seed')
  async seedDemoData() {
    await this.documentsService.seedDemoDocumentsIfEmpty();
    return { success: true, message: 'Reference demo documents seeded.' };
  }

  @Post('documents/reprocess-failed')
  async reprocessFailed() {
    return this.documentsService.reprocessAllFailed();
  }

  @Post('documents/:id/reprocess')
  async reprocessDoc(@Param('id') id: string) {
    return this.documentsService.reprocess(id);
  }

  @Post('reconcile')
  async reconcile(@Body() body: { source_ids?: string[] }) {
    return this.documentsService.reconcileSources(body?.source_ids || []);
  }

  @Post('cross-verify')
  async crossVerify(@Body() dto: CrossVerifyDto) {
    return this.documentsService.crossVerify(dto.document_id_a, dto.document_id_b, dto.tolerance);
  }

  @Get('stats')
  async getStats() {
    return this.documentsService.getStats();
  }
}
