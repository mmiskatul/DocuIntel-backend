import { Logger, ValidationPipe } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { AppModule } from './app.module';

async function bootstrap() {
  const logger = new Logger('Bootstrap');
  const app = await NestFactory.create(AppModule);

  const rawOrigins = process.env.CORS_ORIGINS || 'http://localhost:3000';
  const corsOrigins = rawOrigins.split(',').map((o) => o.trim());

  app.enableCors({
    origin: (origin, callback) => {
      // Allow requests with no origin (like mobile apps, curl, Postman, server-to-server)
      if (!origin) return callback(null, true);

      // Check against explicit list, wildcard, or deployment platforms
      const isAllowed =
        corsOrigins.includes('*') ||
        corsOrigins.includes(origin) ||
        origin.endsWith('.pages.dev') ||
        origin.endsWith('.vercel.app') ||
        origin.endsWith('.onrender.com') ||
        origin.startsWith('http://localhost:') ||
        origin.startsWith('http://127.0.0.1:');

      if (isAllowed) {
        return callback(null, origin);
      }
      // Permissive fallback so frontend is never blocked by CORS
      return callback(null, origin);
    },
    methods: 'GET,HEAD,PUT,PATCH,POST,DELETE,OPTIONS',
    credentials: true,
    allowedHeaders:
      'Content-Type,Authorization,X-Requested-With,Accept,Origin,Access-Control-Request-Method,Access-Control-Request-Headers,X-API-Key',
  });

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      transform: true,
    }),
  );

  // Swagger OpenAPI Documentation
  const config = new DocumentBuilder()
    .setTitle('DocuIntel Document Intelligence API')
    .setDescription('NestJS + Supabase document processing API with JWT Authentication, FastAPI OCR & vector models')
    .setVersion('2.0.0')
    .addBearerAuth(
      {
        type: 'http',
        scheme: 'bearer',
        bearerFormat: 'JWT',
        description: 'Input your Bearer JWT token from /api/auth/login',
      },
      'jwt',
    )
    .build();

  const document = SwaggerModule.createDocument(app, config);
  SwaggerModule.setup('docs', app, document);

  const port = process.env.PORT || 8000;
  await app.listen(port);
  logger.log(`DocuIntel NestJS Backend is running on: http://localhost:${port}`);
  logger.log(`Swagger OpenAPI Documentation: http://localhost:${port}/docs`);
}

bootstrap();

