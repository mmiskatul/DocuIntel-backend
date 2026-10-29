# --- Stage 1: Build ---
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json tsconfig*.json ./
RUN npm ci

COPY src ./src
RUN npm run build

# --- Stage 2: Production Runner ---
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=8000

COPY package*.json ./
RUN npm ci --only=production

COPY --from=builder /app/dist ./dist

EXPOSE 8000

CMD ["node", "dist/main"]

