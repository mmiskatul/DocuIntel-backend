import { Controller, Get } from '@nestjs/common';
import { ApiOperation, ApiTags } from '@nestjs/swagger';
import { OcrService } from './ocr/ocr.service';
import { SupabaseService } from './supabase/supabase.service';

@ApiTags('System & Health')
@Controller()
export class AppController {
  constructor(
    private readonly ocrService: OcrService,
    private readonly supabaseService: SupabaseService,
  ) {}

  @Get()
  @ApiOperation({ summary: 'Backend root discovery and live status' })
  getRoot() {
    this.ocrService.wakeupOcrService();
    return {
      service: 'DocuIntel NestJS Backend',
      version: '2.0.0',
      status: 'online',
      ocr_service: this.ocrService.getOcrUrl(),
      timestamp: new Date().toISOString(),
      docs: '/docs',
    };
  }

  @Get('health')
  @ApiOperation({ summary: 'System health probe (triggers OCR warmup)' })
  async getHealth() {
    return this.getSystemHealth();
  }

  @Get('api/health')
  @ApiOperation({ summary: 'API system health probe (triggers OCR warmup)' })
  async getApiHealth() {
    return this.getSystemHealth();
  }

  @Get('api/wakeup')
  @ApiOperation({ summary: 'Proactive cascade wake-up for both Backend and OCR-Model microservices' })
  async getWakeup() {
    this.ocrService.wakeupOcrService();
    return this.getSystemHealth();
  }

  private async getSystemHealth() {
    const ocrHealth = await this.ocrService.checkHealth();
    const isDbConnected = this.supabaseService.isConnected;

    return {
      status: 'ok',
      backend: 'online',
      ocr_model: ocrHealth,
      database: isDbConnected ? 'connected' : 'memory_fallback',
      timestamp: new Date().toISOString(),
    };
  }
}

