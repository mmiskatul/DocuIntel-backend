import { Injectable, NestMiddleware } from '@nestjs/common';
import { Request, Response, NextFunction } from 'express';
import { OcrService } from '../../ocr/ocr.service';

@Injectable()
export class WarmupMiddleware implements NestMiddleware {
  constructor(private readonly ocrService: OcrService) {}

  use(req: Request, res: Response, next: NextFunction) {
    // Proactively fire background wakeup ping to the OCR service on incoming backend requests
    this.ocrService.wakeupOcrService();
    next();
  }
}
