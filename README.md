# DocuIntel NestJS Backend Service

Production-ready NestJS 10 backend written in **100% TypeScript** integrating with **Supabase** (PostgreSQL), **JWT Authentication**, and the FastAPI OCR microservice.

## Features
- **JWT Authentication**: Secure user registration, password hashing (`bcrypt`), and Bearer JWT token generation (`/api/auth`).
- **Document Processing**: Asynchronous ingestion of PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV files.
- **Supabase Integration**: Stores users, documents, extraction results, validation issues, and vector embeddings in Supabase. Includes an automatic in-memory fallback if Supabase keys are not set.
- **Cross-Verification**: Dedicated PDF-to-PDF and PDF-to-Excel reconciliation engine (`POST /api/cross-verify`).
- **Search**: Hybrid search combining exact metadata/keyword matching and dense vector similarity.
- **Swagger Documentation**: Live OpenAPI Swagger docs with Bearer JWT auth accessible at `http://localhost:8000/docs`.

## Quick Start (Local)

```bash
npm install
npm run start:dev
```
API running on `http://localhost:8000`  
Swagger Docs on `http://localhost:8000/docs`

## Docker Build & Run

```bash
docker build -t docuintel-backend .
docker run -p 8000:8000 --env-file .env docuintel-backend
```

## Vercel Serverless Deployment

This NestJS backend is pre-configured for instant **Vercel Serverless** deployment with 24/7 fast wake-up and zero sleeping:

1. Push your repository to GitHub: [`Task-backend`](https://github.com/mmiskatul/Task-backend).
2. In [vercel.com](https://vercel.com), click **Add New Project** $\rightarrow$ Import `Task-backend`.
3. Under **Environment Variables**, add:
   - `DATABASE_URL`: Your Supabase pooler connection string
   - `SUPABASE_URL`: Your Supabase project URL
   - `SUPABASE_KEY`: Your Supabase anon or service-role key
   - `JWT_SECRET`: Your JWT secret
   - `OCR_SERVICE_URL`: URL to your deployed `ocr-model` service
   - `REDIS_URL`: Your Upstash Redis URL (`rediss://...`)
4. Click **Deploy**! All API endpoints (`/api/*`) and Swagger docs will be live instantly.

## Supabase Schema Setup

1. Open your project on [supabase.com](https://supabase.com).
2. Navigate to **SQL Editor**.
3. Run the SQL script located in [`src/supabase/schema.sql`](src/supabase/schema.sql).
4. Update `SUPABASE_URL` and `SUPABASE_KEY` in `.env`.

## REST API Endpoints

### Authentication (JWT)
| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/auth/register` | Register new user (email, password, name) $\rightarrow$ JWT token |
| `POST` | `/api/auth/login` | Login with credentials $\rightarrow$ JWT access token |
| `GET` | `/api/auth/me` | Protected route returning current user from Bearer JWT token |

### Documents & Cross-Verification
| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/documents` | Batch file upload (multipart `files`) |
| `GET` | `/api/documents` | List & filter documents |
| `GET` | `/api/documents/:id` | Status, stage, and processing progress |
| `GET` | `/api/documents/:id/results` | Extracted invoice data & validation errors |
| `POST` | `/api/cross-verify` | Reconciles two documents (PDF vs PDF or PDF vs Excel) |
| `GET` | `/api/search` | Hybrid keyword & vector semantic search |
| `GET` | `/api/stats` | System counts and Supabase connection health |
