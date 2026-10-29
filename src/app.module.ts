import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { AuthModule } from './auth/auth.module';
import { DocumentsController } from './documents/documents.controller';
import { DocumentsService } from './documents/documents.service';
import { OcrService } from './ocr/ocr.service';
import { SearchController } from './search/search.controller';
import { SearchService } from './search/search.service';
import { SupabaseModule } from './supabase/supabase.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env', '../.env'],
    }),
    SupabaseModule,
    AuthModule,
  ],
  controllers: [DocumentsController, SearchController],
  providers: [OcrService, DocumentsService, SearchService],
})
export class AppModule {}
