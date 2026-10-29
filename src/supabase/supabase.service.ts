import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { Pool } from 'pg';

@Injectable()
export class SupabaseService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(SupabaseService.name);
  public client: SupabaseClient | null = null;
  public pgPool: Pool | null = null;
  public isConnected = false;

  // In-memory fallback if neither Supabase API nor direct DB is reachable
  public memoryDocs: Map<string, any> = new Map();
  public memoryResults: Map<string, any[]> = new Map();
  public memoryEmbeddings: any[] = [];
  public memoryUsers: Map<string, any> = new Map();

  constructor(private configService: ConfigService) {}

  async onModuleInit() {
    await this.initDatabase();
  }

  async onModuleDestroy() {
    if (this.pgPool) {
      await this.pgPool.end();
    }
  }

  private async initDatabase() {
    // 1. Direct PostgreSQL Connection (Supabase Pooler / Direct Postgres)
    let dbUrl = this.configService.get<string>('DATABASE_URL');
    if (dbUrl) {
      // Automatic fix for IPv6-only hostname if running on an IPv4 network:
      // db.PROJECT_REF.supabase.co -> aws-0-eu-north-1.pooler.supabase.com:5432
      if (dbUrl.includes('db.PROJECT_REF.supabase.co')) {
        dbUrl = dbUrl.replace(
          'postgres:REDACTED@db.PROJECT_REF.supabase.co:5432',
          'postgres.PROJECT_REF:REDACTED@aws-0-eu-north-1.pooler.supabase.com:5432',
        );
      }

      try {
        this.pgPool = new Pool({
          connectionString: dbUrl,
          ssl: { rejectUnauthorized: false },
          max: 10,
          idleTimeoutMillis: 30000,
        });

        // Test connection
        const res = await this.pgPool.query('SELECT current_database(), version();');
        this.isConnected = true;
        this.logger.log(`Direct PostgreSQL connected successfully to Supabase! [DB: ${res.rows[0].current_database}]`);
        return;
      } catch (err: any) {
        this.logger.warn(`Direct PostgreSQL connection failed: ${err.message}. Trying Supabase REST API...`);
      }
    }

    // 2. Supabase REST API Client
    const url = this.configService.get<string>('SUPABASE_URL');
    const key =
      this.configService.get<string>('SUPABASE_KEY') ||
      this.configService.get<string>('SUPABASE_SERVICE_ROLE_KEY');

    if (url && key && url.startsWith('http') && !key.includes('your-supabase')) {
      try {
        this.client = createClient(url, key);
        this.isConnected = true;
        this.logger.log(`Supabase REST client initialized for ${url}`);
        return;
      } catch (err: any) {
        this.logger.warn(`Failed to initialize Supabase REST client: ${err.message}`);
      }
    }

    this.logger.log('Database not yet configured. Operating on in-memory fallback store.');
  }

  // ---------------------------------------------------------------------------
  // Documents Table
  // ---------------------------------------------------------------------------
  async saveDocument(doc: any) {
    if (this.pgPool) {
      try {
        const query = `
          INSERT INTO documents (id, filename, file_type, size_bytes, status, stage, progress, page_count, pages_done, error, updated_at)
          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
          ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            stage = EXCLUDED.stage,
            progress = EXCLUDED.progress,
            pages_done = EXCLUDED.pages_done,
            error = EXCLUDED.error,
            updated_at = NOW()
          RETURNING *;
        `;
        const values = [
          doc.id,
          doc.filename,
          doc.file_type || 'application/octet-stream',
          doc.size_bytes || 0,
          doc.status || 'queued',
          doc.stage || 'Uploaded',
          doc.progress || 0.0,
          doc.page_count || 1,
          doc.pages_done || 0,
          doc.error || null,
        ];
        const res = await this.pgPool.query(query, values);
        return res.rows[0];
      } catch (err: any) {
        this.logger.error(`PostgreSQL saveDocument error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client.from('documents').upsert(doc).select().single();
      if (!error && data) return data;
    }

    this.memoryDocs.set(doc.id, { ...doc, updated_at: new Date().toISOString() });
    return this.memoryDocs.get(doc.id);
  }

  async getDocument(id: string) {
    if (this.pgPool) {
      try {
        const res = await this.pgPool.query('SELECT * FROM documents WHERE id = $1;', [id]);
        if (res.rows.length > 0) return res.rows[0];
      } catch (err: any) {
        this.logger.error(`PostgreSQL getDocument error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client.from('documents').select('*').eq('id', id).single();
      if (!error && data) return data;
    }

    return this.memoryDocs.get(id) || null;
  }

  async listDocuments(query: any = {}) {
    if (this.pgPool) {
      try {
        let sql = 'SELECT * FROM documents';
        const params: any[] = [];
        if (query.status) {
          sql += ' WHERE status = $1';
          params.push(query.status);
        }
        sql += ' ORDER BY created_at DESC;';
        const res = await this.pgPool.query(sql, params);
        return res.rows;
      } catch (err: any) {
        this.logger.error(`PostgreSQL listDocuments error: ${err.message}`);
      }
    }

    if (this.client) {
      let q = this.client.from('documents').select('*').order('created_at', { ascending: false });
      if (query.status) q = q.eq('status', query.status);
      const { data, error } = await q;
      if (!error && data) return data;
    }

    const all = Array.from(this.memoryDocs.values()).sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
    if (query.status) return all.filter((d) => d.status === query.status);
    return all;
  }

  async deleteDocument(id: string) {
    if (this.pgPool) {
      try {
        await this.pgPool.query('DELETE FROM validation_issues WHERE document_id = $1', [id]);
        await this.pgPool.query('DELETE FROM extraction_results WHERE document_id = $1', [id]);
        await this.pgPool.query('DELETE FROM document_embeddings WHERE document_id = $1', [id]);
        await this.pgPool.query('DELETE FROM documents WHERE id = $1', [id]);
      } catch (err: any) {
        this.logger.error(`PostgreSQL deleteDocument error: ${err.message}`);
      }
    }
    if (this.client) {
      await this.client.from('extraction_results').delete().eq('document_id', id);
      await this.client.from('document_embeddings').delete().eq('document_id', id);
      await this.client.from('documents').delete().eq('id', id);
    }
    this.memoryDocs.delete(id);
    this.memoryResults.delete(id);
    return { success: true, id };
  }

  async deleteAllDocuments() {
    if (this.pgPool) {
      try {
        await this.pgPool.query('DELETE FROM validation_issues');
        await this.pgPool.query('DELETE FROM extraction_results');
        await this.pgPool.query('DELETE FROM document_embeddings');
        await this.pgPool.query('DELETE FROM documents');
      } catch (err: any) {
        this.logger.error(`PostgreSQL deleteAllDocuments error: ${err.message}`);
      }
    }
    if (this.client) {
      await this.client.from('extraction_results').delete().neq('id', 0);
      await this.client.from('document_embeddings').delete().neq('id', 0);
      await this.client.from('documents').delete().neq('id', '');
    }
    this.memoryDocs.clear();
    this.memoryResults.clear();
    return { success: true };
  }

  // ---------------------------------------------------------------------------
  // Extraction Results Table
  // ---------------------------------------------------------------------------
  async saveExtractionResult(result: any) {
    if (this.pgPool) {
      try {
        const query = `
          INSERT INTO extraction_results (
            document_id, invoice_number, invoice_date, supplier_name, customer_name,
            subtotal, tax, total, currency, data, confidence, overall_confidence,
            needs_review, reviewed, created_at
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, NOW())
          RETURNING *;
        `;
        const values = [
          result.document_id,
          result.invoice_number || null,
          result.invoice_date || null,
          result.supplier_name || null,
          result.customer_name || null,
          result.subtotal || null,
          result.tax || null,
          result.total || null,
          result.currency || 'USD',
          JSON.stringify(result.data || {}),
          JSON.stringify(result.confidence || {}),
          result.overall_confidence || 0.0,
          result.needs_review || false,
          result.reviewed || false,
        ];
        const res = await this.pgPool.query(query, values);
        return res.rows[0];
      } catch (err: any) {
        this.logger.error(`PostgreSQL saveExtractionResult error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client.from('extraction_results').upsert(result).select().single();
      if (!error && data) return data;
    }

    const list = this.memoryResults.get(result.document_id) || [];
    list.push(result);
    this.memoryResults.set(result.document_id, list);
    return result;
  }

  async getExtractionResults(documentId: string) {
    if (this.pgPool) {
      try {
        const res = await this.pgPool.query(
          'SELECT * FROM extraction_results WHERE document_id = $1 ORDER BY id ASC;',
          [documentId],
        );
        return res.rows;
      } catch (err: any) {
        this.logger.error(`PostgreSQL getExtractionResults error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client
        .from('extraction_results')
        .select('*, issues:validation_issues(*)')
        .eq('document_id', documentId);
      if (!error && data) return data;
    }

    return this.memoryResults.get(documentId) || [];
  }

  // ---------------------------------------------------------------------------
  // Vector Database Embeddings (pgvector)
  // ---------------------------------------------------------------------------
  async saveEmbedding(record: {
    document_id: string;
    chunk_text: string;
    embedding?: number[];
    vector?: number[];
    created_at?: string;
  }) {
    const vec = record.embedding || record.vector || [];
    const vecString = `[${vec.join(',')}]`;

    if (this.pgPool) {
      try {
        await this.pgPool.query(
          `INSERT INTO document_embeddings (document_id, chunk_text, embedding, vector, created_at)
           VALUES ($1, $2, $3::vector, $4::jsonb, NOW());`,
          [record.document_id, record.chunk_text, vecString, JSON.stringify(vec)],
        );
        return;
      } catch (err: any) {
        this.logger.warn(`PostgreSQL saveEmbedding error: ${err.message}`);
      }
    }

    if (this.client) {
      try {
        await this.client.from('document_embeddings').insert({
          document_id: record.document_id,
          chunk_text: record.chunk_text,
          embedding: vec,
          vector: vec,
        });
      } catch (err: any) {
        this.logger.debug(`Supabase REST saveEmbedding: ${err.message}`);
      }
    }

    this.memoryEmbeddings.push({ ...record, vector: vec });
  }

  async searchVectorEmbeddings(
    queryEmbedding: number[],
    limit = 10,
    threshold = 0.25,
  ): Promise<Array<{ document_id: string; chunk_text: string; similarity: number }>> {
    const vecString = `[${queryEmbedding.join(',')}]`;

    // 1. Direct PostgreSQL Native pgvector Cosine Distance Query
    if (this.pgPool) {
      try {
        const query = `
          SELECT
            document_id,
            chunk_text,
            ROUND((1 - (embedding <=> $1::vector))::numeric, 3)::float AS similarity
          FROM document_embeddings
          WHERE embedding IS NOT NULL
            AND (1 - (embedding <=> $1::vector)) >= $2
          ORDER BY embedding <=> $1::vector ASC
          LIMIT $3;
        `;
        const res = await this.pgPool.query(query, [vecString, threshold, limit]);
        if (res.rows.length > 0) return res.rows;
      } catch (err: any) {
        this.logger.warn(`PostgreSQL vector search error: ${err.message}`);
      }
    }

    // 2. Supabase REST match_documents RPC
    if (this.client) {
      try {
        const { data, error } = await this.client.rpc('match_documents', {
          query_embedding: queryEmbedding,
          match_threshold: threshold,
          match_count: limit,
        });
        if (!error && data && data.length > 0) return data;
      } catch (err: any) {
        this.logger.debug(`Supabase RPC fallback: ${err.message}`);
      }
    }

    // 3. In-memory Cosine Similarity Fallback
    return this.memoryEmbeddings
      .map((emb: any) => {
        const vec = emb.embedding || emb.vector;
        const sim = Array.isArray(vec) ? this.cosineSim(queryEmbedding, vec) : 0;
        return {
          document_id: emb.document_id,
          chunk_text: emb.chunk_text,
          similarity: Math.round(sim * 1000) / 1000,
        };
      })
      .filter((r) => r.similarity >= threshold)
      .sort((a, b) => b.similarity - a.similarity)
      .slice(0, limit);
  }

  private cosineSim(a: number[], b: number[]): number {
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    if (normA === 0 || normB === 0) return 0;
    return dot / (Math.sqrt(normA) * Math.sqrt(normB));
  }

  // ---------------------------------------------------------------------------
  // Users Table (JWT Authentication)
  // ---------------------------------------------------------------------------
  async findUserByEmail(email: string) {
    const normalized = email.toLowerCase().trim();

    if (this.pgPool) {
      try {
        const res = await this.pgPool.query('SELECT * FROM users WHERE LOWER(email) = $1 LIMIT 1;', [normalized]);
        if (res.rows.length > 0) return res.rows[0];
      } catch (err: any) {
        this.logger.error(`PostgreSQL findUserByEmail error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client
        .from('users')
        .select('*')
        .eq('email', normalized)
        .maybeSingle();
      if (!error && data) return data;
    }

    for (const u of this.memoryUsers.values()) {
      if (u.email.toLowerCase() === normalized) return u;
    }
    return null;
  }

  async findUserById(id: string) {
    if (this.pgPool) {
      try {
        const res = await this.pgPool.query('SELECT * FROM users WHERE id = $1 LIMIT 1;', [id]);
        if (res.rows.length > 0) return res.rows[0];
      } catch (err: any) {
        this.logger.error(`PostgreSQL findUserById error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client.from('users').select('*').eq('id', id).maybeSingle();
      if (!error && data) return data;
    }

    return this.memoryUsers.get(id) || null;
  }

  async createUser(user: any) {
    if (this.pgPool) {
      try {
        const query = `
          INSERT INTO users (id, email, password_hash, name, role, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
          RETURNING *;
        `;
        const res = await this.pgPool.query(query, [
          user.id,
          user.email.toLowerCase().trim(),
          user.password_hash,
          user.name || null,
          user.role || 'user',
        ]);
        return res.rows[0];
      } catch (err: any) {
        this.logger.error(`PostgreSQL createUser error: ${err.message}`);
      }
    }

    if (this.client) {
      const { data, error } = await this.client.from('users').insert(user).select().single();
      if (!error && data) return data;
    }

    this.memoryUsers.set(user.id, user);
    return user;
  }
}
