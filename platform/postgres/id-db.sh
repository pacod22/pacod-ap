#!/bin/sh
# pacod-id's role and database on the shared PostgreSQL (pacod-postgres), for the pacod-id that
# pacod-ap's own stack runs (pacod-api/docker-compose.standalone.yml): a role "pacod_id" that is
# not a superuser and owns the database "pacod_id". pacod-id creates its tables there
# (migrations run at start). The same as pacod-id's own platform/postgres/id-db.sh; the full
# stack (pacod-demo) runs that one instead.
#
# pacod-db-init runs this at every start, before pacod-id, as a superuser through libpq's PG*
# variables (PGHOST, PGUSER, PGPASSWORD). Safe to run again: it creates what is missing and sets
# the role's password to PID_DB_PASSWORD. The password is read with psql's \getenv, so it is on no
# command line, and nothing secret is printed.

set -eu

: "${PGHOST:?the PostgreSQL host, with PGUSER and PGPASSWORD of a superuser}"
: "${PID_DB_PASSWORD:?the password for the role pacod_id: run make secrets-init (deploy/secrets/id.env)}"

psql -v ON_ERROR_STOP=1 -q -d postgres <<'SQL'
\getenv pw PID_DB_PASSWORD
SELECT format('CREATE ROLE pacod_id LOGIN PASSWORD %L', :'pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pacod_id') \gexec
SELECT format('ALTER ROLE pacod_id LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'pw')
 WHERE current_user <> 'pacod_id' \gexec
SELECT 'CREATE DATABASE pacod_id OWNER pacod_id'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pacod_id') \gexec
REVOKE ALL ON DATABASE pacod_id FROM PUBLIC;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE pacod_id TO pacod_id;
SQL

echo "id-db: the role and the database pacod_id are in place"
