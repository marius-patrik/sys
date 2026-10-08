-- Provider configuration and model policy belong in PostgreSQL.
-- Actual credentials are encrypted before leaving the application process.
CREATE TABLE integration_credentials (
  scope_id text NOT NULL,
  name text NOT NULL,
  nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
  ciphertext bytea NOT NULL CHECK(octet_length(ciphertext)>=16),
  key_version integer NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(scope_id,name)
);
CREATE TABLE integration_endpoints (
  scope_id text NOT NULL,
  service text NOT NULL,
  base_url text NOT NULL,
  credential_name text,
  revision integer NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(scope_id,service)
);
CREATE TABLE model_selections (
  scope_id text NOT NULL,
  purpose text NOT NULL CHECK(purpose IN ('answer','compose','default')),
  model_id text NOT NULL CHECK(length(model_id) BETWEEN 1 AND 256),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(scope_id,purpose)
);
