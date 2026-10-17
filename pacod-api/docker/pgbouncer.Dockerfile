FROM alpine:3.20

RUN apk add --no-cache pgbouncer su-exec && \
    addgroup -S pgbouncer && \
    adduser -S -G pgbouncer pgbouncer

COPY docker/pgbouncer/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5432
ENTRYPOINT ["/entrypoint.sh"]