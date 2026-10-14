#!/bin/sh
set -eu
# The application role owns its schema, but is not a superuser or role/database creator.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DB_PASSWORD
CREATE ROLE commerce LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE SCHEMA commerce AUTHORIZATION commerce;
ALTER ROLE commerce SET search_path TO commerce;
GRANT CONNECT ON DATABASE commerce TO commerce;
SQL
