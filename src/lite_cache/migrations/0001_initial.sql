-- lite-cache tables (design §4). Runs with search_path = <schema>, public.
-- scope is C-collated so the prefix invalidation (design §3.3) compares bytes, whatever the
-- database's default collation.

CREATE TABLE entries (
    key text PRIMARY KEY,
    scope text COLLATE "C" NOT NULL,
    role text NOT NULL,
    backend text NOT NULL,
    model text NOT NULL,
    question text,
    kind text NOT NULL CHECK (kind IN ('text', 'structured')),
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    input_tokens integer,
    output_tokens integer,
    reasoning_tokens integer,
    cost_usd double precision,
    created_at timestamptz NOT NULL,
    expires_at timestamptz
);
CREATE INDEX entries_scope ON entries (scope COLLATE "C");
CREATE INDEX entries_role ON entries (role);
CREATE INDEX entries_expires ON entries (expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE stats (
    day date NOT NULL,
    scope text COLLATE "C" NOT NULL,
    role text NOT NULL,
    outcome text NOT NULL,
    n bigint NOT NULL DEFAULT 0,
    saved_input_tokens bigint NOT NULL DEFAULT 0,
    saved_output_tokens bigint NOT NULL DEFAULT 0,
    spent_input_tokens bigint NOT NULL DEFAULT 0,
    spent_output_tokens bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (day, scope, role, outcome)
);
