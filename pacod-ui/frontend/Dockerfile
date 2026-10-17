# syntax=docker/dockerfile:1.7
# ============================================================
# Multi-stage Dockerfile for the SvelteKit frontend
# ============================================================

FROM node:22-alpine AS builder
WORKDIR /app

RUN apk --no-cache add curl

# Install deps first for cache efficiency. The @pacod/* packages are vendored
# tarballs that package.json references as `file:vendor/…`, so they must be in
# place before `npm ci`.
COPY package.json package-lock.json* ./
COPY vendor/ ./vendor/
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Build. The SSR + client bundle (incl. the agent editor's @xyflow/svelte)
# exceeds V8's default heap inside a container — raise it for this step only.
#
# ID_ISSUER (pacod-id's public issuer) is read by svelte.config.js at BUILD time:
# SvelteKit's CSRF check, which runs before any hook, takes its trusted origins
# from the build, and pacod-id's back-channel logout is a cross-site form POST
# carrying the issuer as its Origin. Without it that POST is refused with 403.
ARG ID_ISSUER
COPY . .
RUN NODE_OPTIONS=--max-old-space-size=4096 npm run build

# Prune devDeps for the runtime image
RUN npm prune --omit=dev


FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Non-root user
RUN addgroup -S app && adduser -S app -G app

COPY --from=builder --chown=app:app /app/build ./build
COPY --from=builder --chown=app:app /app/node_modules ./node_modules
COPY --from=builder --chown=app:app /app/package.json ./package.json

USER app
EXPOSE 3000

# Health check on /health: pages answer a request on any origin other than the
# public one with a redirect to it (lib/server/identity.ts), /health does not.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:3000/health || exit 1

CMD ["node", "build"]
