import os

import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://app:app@localhost:54399/yawalign",
)


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS yaw_logs (
    id serial PRIMARY KEY,
    turbine_code text NOT NULL,
    yaw_err_deg double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);

CREATE TABLE IF NOT EXISTS yaw_merges (
    id serial PRIMARY KEY,
    new_log_id integer NOT NULL REFERENCES yaw_logs(id),
    turbine_code text NOT NULL,
    verdict text NOT NULL,
    source_count integer NOT NULL,
    avg_yaw_err_deg double precision NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS yaw_merge_items (
    merge_id integer NOT NULL REFERENCES yaw_merges(id) ON DELETE CASCADE,
    log_id integer NOT NULL REFERENCES yaw_logs(id),
    PRIMARY KEY (merge_id, log_id)
);
"""
