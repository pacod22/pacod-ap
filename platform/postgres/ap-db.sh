#!/bin/sh
# pacod-ap's role and database on the shared PostgreSQL (pacod-postgres): the role
# POSTGRES_USER (default "pacod"), which owns the app's database POSTGRES_DB (default "pacod").
# The app creates its tables at start (create_all + its migration ledger); this adds what the
# app does not make itself: the text search configuration bulgarian_simple.
#
# pacod-db-init runs this at every start, before pacod-ap's services, as a superuser through
# libpq's PG* variables (PGHOST, PGUSER, PGPASSWORD). Safe to run again: it creates what is
# missing and sets the role's password to AP_DB_PASSWORD (default: POSTGRES_PASSWORD), unless
# the role is the connecting superuser itself — pacod-ap's own stack connects as "pacod", its
# superuser since the beginning, which is left as it is. Elsewhere (the full stack) the role is
# made LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE. The password is read with psql's \getenv, so
# it is on no command line, and nothing secret is printed. The same file serves pacod-ap's own
# stack (pacod-api/docker-compose.standalone.yml) and the full stack (pacod-demo).

set -eu

: "${PGHOST:?the PostgreSQL host, with PGUSER and PGPASSWORD of a superuser}"
AP_DB_PASSWORD="${AP_DB_PASSWORD:-${POSTGRES_PASSWORD:-}}"
: "${AP_DB_PASSWORD:?the password for the role of pacod-ap (AP_DB_PASSWORD, or POSTGRES_PASSWORD)}"
export AP_DB_PASSWORD

role="${POSTGRES_USER:-pacod}"
db="${POSTGRES_DB:-pacod}"

psql -v ON_ERROR_STOP=1 -q -d postgres -v role="$role" -v db="$db" <<'SQL'
\getenv pw AP_DB_PASSWORD
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'role', :'pw')
 WHERE current_user <> :'role' \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SELECT format('GRANT CONNECT, CREATE, TEMPORARY ON DATABASE %I TO %I', :'db', :'role') \gexec
SQL

# The text search configuration for Bulgarian content (formerly docker/postgres/init.sql, run
# once at the first start). The 'simple' configuration handles non-stemmed tokenization fine for
# the corpus; a production deployment would install hunspell dictionaries and set up a proper
# bulgarian_dict. Tables and indexes are the app's (SQLAlchemy create_all).
psql -v ON_ERROR_STOP=1 -q -d "$db" -v role="$role" <<'SQL'
SELECT 'CREATE TEXT SEARCH CONFIGURATION public.bulgarian_simple ( COPY = pg_catalog.simple )'
 WHERE NOT EXISTS (SELECT FROM pg_ts_config
                    WHERE cfgname = 'bulgarian_simple' AND cfgnamespace = 'public'::regnamespace) \gexec
SELECT format('ALTER TEXT SEARCH CONFIGURATION public.bulgarian_simple OWNER TO %I', :'role')
 WHERE current_user <> :'role' \gexec
SQL

echo "ap-db: the role $role and the database $db are in place"
