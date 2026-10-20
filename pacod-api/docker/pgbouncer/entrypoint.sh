#!/bin/sh
set -e

mkdir -p /etc/pgbouncer

# Generate pgbouncer.ini from environment variables.
cat > /etc/pgbouncer/pgbouncer.ini << EOF
[databases]
${PGBOUNCER_DATABASE:-pacod} = host=${POSTGRESQL_HOST:-pacod-postgres} port=${POSTGRESQL_PORT:-5432} dbname=${POSTGRESQL_DATABASE:-pacod}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 5432
auth_type = ${PGBOUNCER_AUTH_TYPE:-plain}
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = ${PGBOUNCER_POOL_MODE:-transaction}
max_client_conn = ${PGBOUNCER_MAX_CLIENT_CONN:-200}
default_pool_size = ${PGBOUNCER_DEFAULT_POOL_SIZE:-25}
server_reset_query = DISCARD ALL
; The metrics exporter (ap-pgbouncer-exporter) reads SHOW STATS on the admin console as the app's
; user, and its driver sends extra_float_digits at connect, which pgbouncer refuses otherwise.
stats_users = ${POSTGRESQL_USERNAME:-pacod}
ignore_startup_parameters = extra_float_digits
log_connections = 0
log_disconnections = 0
EOF

# Generate userlist.txt (plain-text password file).
cat > /etc/pgbouncer/userlist.txt << EOF
"${POSTGRESQL_USERNAME:-pacod}" "${POSTGRESQL_PASSWORD}"
EOF

# Hand ownership to the pgbouncer OS user (created by the apk package)
# then drop root before starting the daemon.
chown -R pgbouncer:pgbouncer /etc/pgbouncer
exec su-exec pgbouncer pgbouncer /etc/pgbouncer/pgbouncer.ini