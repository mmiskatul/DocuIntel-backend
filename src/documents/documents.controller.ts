import {
  Body,
  Controller,
  Delete,
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
      res.setHeader('Content-Type', 'image/svg+xml');
      return res.send(`
        <svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400" style="background:#f8fafc; font-family:sans-serif;">
          <rect width="100%" height="100%" fill="#ffffff" stroke="#e2e8f0" stroke-width="2"/>
          <text x="300" y="180" font-size="18" font-weight="600" fill="#475569" text-anchor="middle">Original File Preview Unavailable</text>
          <text x="300" y="215" font-size="13" fill="#94a3b8" text-anchor="middle">File binary cache expired or document was uploaded without retention</text>
        </svg>
      `);
    }
    res.setHeader('Content-Type', file.mimetype);
    res.setHeader('Content-Disposition', `inline; filename="${file.filename}"`);
    return res.send(file.buffer);
  }

  @Delete('documents/:id')
  async deleteDocument(@Param('id') id: string) {
    return this.documentsService.deleteDocument(id);
  }

  @Delete('documents')
  async deleteAllDocuments() {
    return this.documentsService.deleteAllDocuments();
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
