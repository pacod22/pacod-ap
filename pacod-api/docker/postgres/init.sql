-- Postgres bootstrap. Runs once on first container start.
-- Configures a Bulgarian text-search dictionary for FTS on Bulgarian content.

-- The 'simple' configuration handles non-stemmed tokenization fine for our
-- corpus. A production deployment would install hunspell dictionaries and
-- set up a proper bulgarian_dict.

CREATE TEXT SEARCH CONFIGURATION bulgarian_simple ( COPY = simple );

-- Useful indexes (will be created by SQLAlchemy too, but harmless here).
-- Left empty by design; let SQLAlchemy create_all be the source of truth.
