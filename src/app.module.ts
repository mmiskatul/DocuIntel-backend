import { MiddlewareConsumer, Module, NestModule } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { AppController } from './app.controller';
import { AuthModule } from './auth/auth.module';
import { WarmupMiddleware } from './common/middleware/warmup.middleware';
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
  controllers: [AppController, DocumentsController, SearchController],
  providers: [OcrService, DocumentsService, SearchService],
})
export class AppModule implements NestModule {
  configure(consumer: MiddlewareConsumer) {
    consumer.apply(WarmupMiddleware).forRoutes('*');
  }
}
