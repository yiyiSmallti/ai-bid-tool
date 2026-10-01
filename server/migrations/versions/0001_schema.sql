CREATE TABLE orgs (
	org_id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT org_self_scope CHECK (id = org_id), 
	UNIQUE (org_id)
);

CREATE TABLE users (
	email VARCHAR(254) NOT NULL, 
	password_hash TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE memberships (
	user_id UUID NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	active BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, user_id), 
	CONSTRAINT membership_role CHECK (role IN ('admin', 'bidder', 'technical', 'viewer')), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_memberships_org_id ON memberships (org_id);

CREATE TABLE api_tokens (
	user_id UUID NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	digest VARCHAR(64) NOT NULL, 
	encrypted_secret TEXT NOT NULL, 
	scopes JSONB NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, digest), 
	FOREIGN KEY(org_id, user_id) REFERENCES memberships (org_id, user_id), 
	CONSTRAINT token_forbidden_scopes CHECK (NOT (scopes ? 'evidence:confirm') AND NOT (scopes ? 'export')), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_api_tokens_org_id ON api_tokens (org_id);

CREATE TABLE tasks (
	name VARCHAR(200) NOT NULL, 
	tender_number VARCHAR(100), 
	deadline TIMESTAMP WITH TIME ZONE, 
	budget_usd NUMERIC(12, 4), 
	created_by UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_tasks_org_id ON tasks (org_id);

CREATE TABLE documents (
	task_id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	storage_key TEXT NOT NULL, 
	media_type VARCHAR(100) NOT NULL, 
	page_count INTEGER, 
	status VARCHAR(20) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, sha256), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_documents_org_id ON documents (org_id);

CREATE TABLE usage_records (
	task_id UUID NOT NULL, 
	provider VARCHAR(100) NOT NULL, 
	model VARCHAR(100) NOT NULL, 
	version VARCHAR(100) NOT NULL, 
	duration_ms INTEGER NOT NULL, 
	tokens INTEGER NOT NULL, 
	ocr_pages INTEGER NOT NULL, 
	usd NUMERIC(16, 8), 
	test_only BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_usage_records_org_id ON usage_records (org_id);

CREATE TABLE chunks (
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	page INTEGER NOT NULL, 
	text TEXT NOT NULL, 
	ocr BOOLEAN NOT NULL, 
	citation_verified BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, document_id, page), 
	FOREIGN KEY(org_id, document_id) REFERENCES documents (org_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	CONSTRAINT chunk_page_positive CHECK (page > 0), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_chunks_org_id ON chunks (org_id);

CREATE TABLE jobs (
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	cache_key VARCHAR(64) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	queue_id INTEGER, 
	attempts INTEGER NOT NULL, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	result JSONB NOT NULL, 
	error JSONB, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, cache_key), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, document_id) REFERENCES documents (org_id, id), 
	CONSTRAINT job_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX jobs_state ON jobs (org_id, status);

CREATE INDEX ix_jobs_org_id ON jobs (org_id);

CREATE TABLE requirements (
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	chunk_id UUID NOT NULL, 
	page INTEGER NOT NULL, 
	quote TEXT NOT NULL, 
	text TEXT NOT NULL, 
	category VARCHAR(20) NOT NULL, 
	starred BOOLEAN NOT NULL, 
	condition JSONB NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, fingerprint), 
	FOREIGN KEY(org_id, document_id) REFERENCES documents (org_id, id), 
	FOREIGN KEY(org_id, chunk_id) REFERENCES chunks (org_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	CONSTRAINT requirement_citation CHECK (page > 0 AND length(quote) > 0), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
);

CREATE INDEX ix_requirements_org_id ON requirements (org_id);
