"""Initial TalentScope schema (self-contained baseline).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-04

This revision embeds the MVP baseline DDL that matches ``db/schema.sql``.
It does **not** read ``db/schema.sql`` at runtime so Backend Docker images
(which only COPY ``backend/``) can run ``alembic upgrade head`` on an empty DB.

``db/schema.sql`` remains the design baseline / reference Source of Truth.
Subsequent schema changes must be new Alembic revisions — do not re-apply
``schema.sql`` in production.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

REQUIRED_EXTENSIONS = ("pgcrypto", "vector", "pg_trgm")

# Immutable snapshot of db/schema.sql (extensions + BEGIN/COMMIT stripped).
BASELINE_DDL = r'''
-- TalentScope MVP PostgreSQL baseline schema
-- Target: PostgreSQL 16+ / pgvector / pg_trgm
-- Embedding dimension: 1024 (BGE-M3)
-- This file is a design baseline. Once implementation starts, Alembic migrations become the operational source of schema changes.



-- -----------------------------------------------------------------------------
-- Common helper
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- Users / Auth
-- -----------------------------------------------------------------------------

CREATE TABLE app_user (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    login_id VARCHAR(100) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    department VARCHAR(200),
    role VARCHAR(20) NOT NULL DEFAULT 'USER'
        CHECK (role IN ('USER', 'ADMIN')),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE')),
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_app_user_updated_at
BEFORE UPDATE ON app_user
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- Code master / aliases
-- -----------------------------------------------------------------------------

CREATE TABLE code_master (
    code VARCHAR(100) PRIMARY KEY,
    code_type VARCHAR(30) NOT NULL
        CHECK (code_type IN ('JOB', 'TECH', 'EXP', 'BIZ', 'CUSTOMER_TYPE', 'DOC_TYPE')),
    parent_code VARCHAR(100) REFERENCES code_master(code) ON DELETE RESTRICT,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_code_master_type_parent
    ON code_master (code_type, parent_code, sort_order);

CREATE TRIGGER trg_code_master_updated_at
BEFORE UPDATE ON code_master
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE code_alias (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE CASCADE,
    alias VARCHAR(300) NOT NULL,
    normalized_alias VARCHAR(300) NOT NULL,
    language VARCHAR(10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (code, normalized_alias)
);

CREATE INDEX idx_code_alias_normalized
    ON code_alias USING GIN (normalized_alias gin_trgm_ops);

-- -----------------------------------------------------------------------------
-- Person / Confirmed profile
-- -----------------------------------------------------------------------------

CREATE TABLE person (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED', 'DELETED')),
    created_by UUID REFERENCES app_user(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_person_status ON person (status) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_person_updated_at
BEFORE UPDATE ON person
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE person_profile (
    person_id UUID PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE,
    name VARCHAR(150) NOT NULL,
    birth_year SMALLINT,
    phone VARCHAR(50),
    email VARCHAR(255),
    address_region VARCHAR(200),
    affiliation_company VARCHAR(300),
    department VARCHAR(200),
    current_title VARCHAR(200),
    employment_type VARCHAR(50),
    technical_grade VARCHAR(30)
        CHECK (technical_grade IS NULL OR technical_grade IN ('BEGINNER', 'INTERMEDIATE', 'ADVANCED', 'EXPERT', 'UNKNOWN')),
    career_start_date DATE,
    career_calculated_months INTEGER CHECK (career_calculated_months IS NULL OR career_calculated_months >= 0),
    career_document_value VARCHAR(100),
    career_confirmed_months INTEGER CHECK (career_confirmed_months IS NULL OR career_confirmed_months >= 0),
    profile_summary TEXT,
    profile_version INTEGER NOT NULL DEFAULT 1 CHECK (profile_version > 0),
    profile_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_person_profile_name_trgm
    ON person_profile USING GIN (name gin_trgm_ops);
CREATE INDEX idx_person_profile_company_trgm
    ON person_profile USING GIN (affiliation_company gin_trgm_ops);
CREATE INDEX idx_person_profile_email_lower
    ON person_profile (LOWER(email)) WHERE email IS NOT NULL;
CREATE INDEX idx_person_profile_phone
    ON person_profile (phone) WHERE phone IS NOT NULL;
CREATE INDEX idx_person_profile_grade
    ON person_profile (technical_grade);

CREATE TRIGGER trg_person_profile_updated_at
BEFORE UPDATE ON person_profile
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- Registration / temporary upload
-- -----------------------------------------------------------------------------

CREATE TABLE upload_session (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(30) NOT NULL DEFAULT 'UPLOADING'
        CHECK (status IN ('UPLOADING', 'IDENTIFYING', 'IDENTIFIED', 'RESOLVED', 'CANCELLED', 'EXPIRED')),
    identified_name VARCHAR(150),
    identified_company VARCHAR(300),
    identified_phone VARCHAR(50),
    identified_email VARCHAR(255),
    duplicate_result_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    resolved_person_id UUID REFERENCES person(id) ON DELETE SET NULL,
    created_by UUID REFERENCES app_user(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_upload_session_status_created
    ON upload_session (status, created_at DESC);

CREATE TABLE upload_temp_file (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_session_id UUID NOT NULL REFERENCES upload_session(id) ON DELETE CASCADE,
    document_type_code VARCHAR(100) REFERENCES code_master(code) ON DELETE RESTRICT,
    original_filename VARCHAR(500) NOT NULL,
    temp_storage_key TEXT NOT NULL,
    mime_type VARCHAR(200),
    extension VARCHAR(30),
    file_size BIGINT NOT NULL CHECK (file_size >= 0),
    sha256 CHAR(64) NOT NULL,
    validation_status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (validation_status IN ('PENDING', 'VALID', 'INVALID', 'ENCRYPTED', 'DUPLICATE')),
    validation_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_upload_temp_session ON upload_temp_file (upload_session_id);
CREATE INDEX idx_upload_temp_sha256 ON upload_temp_file (sha256);

-- -----------------------------------------------------------------------------
-- Person capabilities / history
-- -----------------------------------------------------------------------------

CREATE TABLE person_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    job_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    job_type VARCHAR(20) NOT NULL
        CHECK (job_type IN ('PRIMARY', 'SECONDARY', 'EXPERIENCE')),
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (person_id, job_code, job_type)
);

CREATE INDEX idx_person_job_code_person ON person_job (job_code, person_id);
CREATE INDEX idx_person_job_person_type ON person_job (person_id, job_type);

CREATE TABLE person_skill (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    tech_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    last_used_year SMALLINT,
    experience_months INTEGER CHECK (experience_months IS NULL OR experience_months >= 0),
    is_representative BOOLEAN NOT NULL DEFAULT FALSE,
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (person_id, tech_code)
);

CREATE INDEX idx_person_skill_code_person ON person_skill (tech_code, person_id);
CREATE INDEX idx_person_skill_person_recent ON person_skill (person_id, last_used_year DESC);

CREATE TABLE person_expertise (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    exp_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    evidence_type VARCHAR(20) NOT NULL DEFAULT 'EXPLICIT'
        CHECK (evidence_type IN ('EXPLICIT', 'INFERRED')),
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (person_id, exp_code)
);

CREATE INDEX idx_person_expertise_code_person ON person_expertise (exp_code, person_id);

CREATE TABLE employment_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    company_name VARCHAR(300) NOT NULL,
    department VARCHAR(200),
    title VARCHAR(200),
    start_date DATE,
    end_date DATE,
    responsibilities TEXT,
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE INDEX idx_employment_person_period
    ON employment_history (person_id, start_date DESC);

CREATE TRIGGER trg_employment_history_updated_at
BEFORE UPDATE ON employment_history
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE education (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    school_name VARCHAR(300) NOT NULL,
    major VARCHAR(300),
    degree VARCHAR(100),
    start_date DATE,
    end_date DATE,
    status VARCHAR(100),
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE INDEX idx_education_person ON education (person_id);

CREATE TRIGGER trg_education_updated_at
BEFORE UPDATE ON education
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE certification (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    certification_name VARCHAR(300) NOT NULL,
    issuer VARCHAR(300),
    acquired_date DATE,
    expiry_date DATE,
    certificate_no VARCHAR(200),
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (expiry_date IS NULL OR acquired_date IS NULL OR expiry_date >= acquired_date)
);

CREATE INDEX idx_certification_person ON certification (person_id);
CREATE INDEX idx_certification_name_trgm
    ON certification USING GIN (certification_name gin_trgm_ops);

CREATE TRIGGER trg_certification_updated_at
BEFORE UPDATE ON certification
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- Projects
-- -----------------------------------------------------------------------------

CREATE TABLE project (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    project_name VARCHAR(500) NOT NULL,
    customer_name VARCHAR(300),
    start_date DATE,
    end_date DATE,
    duration_months INTEGER CHECK (duration_months IS NULL OR duration_months >= 0),
    responsibilities TEXT,
    project_summary TEXT,
    source_type VARCHAR(30) NOT NULL DEFAULT 'AI_CONFIRMED'
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')),
    source_analysis_run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE INDEX idx_project_person_period
    ON project (person_id, start_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_project_name_trgm
    ON project USING GIN (project_name gin_trgm_ops) WHERE deleted_at IS NULL;
CREATE INDEX idx_project_customer_trgm
    ON project USING GIN (customer_name gin_trgm_ops) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_project_updated_at
BEFORE UPDATE ON project
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE project_job (
    project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    job_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, job_code)
);

CREATE INDEX idx_project_job_code ON project_job (job_code, project_id);

CREATE TABLE project_skill (
    project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    tech_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, tech_code)
);

CREATE INDEX idx_project_skill_code ON project_skill (tech_code, project_id);

CREATE TABLE project_expertise (
    project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    exp_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    evidence_type VARCHAR(20) NOT NULL DEFAULT 'EXPLICIT'
        CHECK (evidence_type IN ('EXPLICIT', 'INFERRED')),
    PRIMARY KEY (project_id, exp_code)
);

CREATE INDEX idx_project_expertise_code ON project_expertise (exp_code, project_id);

CREATE TABLE project_business_domain (
    project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    biz_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, biz_code)
);

CREATE INDEX idx_project_biz_code ON project_business_domain (biz_code, project_id);

CREATE TABLE project_customer_type (
    project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    customer_type_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, customer_type_code)
);

CREATE INDEX idx_project_customer_type_code
    ON project_customer_type (customer_type_code, project_id);

-- Person-level BIZ / customer type are derived from confirmed projects.
CREATE VIEW vw_person_business_domain AS
SELECT DISTINCT p.person_id, pbd.biz_code
FROM project p
JOIN project_business_domain pbd ON pbd.project_id = p.id
WHERE p.deleted_at IS NULL;

CREATE VIEW vw_person_customer_type AS
SELECT DISTINCT p.person_id, pct.customer_type_code
FROM project p
JOIN project_customer_type pct ON pct.project_id = p.id
WHERE p.deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- Documents / versions / pages / chunks
-- -----------------------------------------------------------------------------

CREATE TABLE document_group (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    document_type_code VARCHAR(100) NOT NULL REFERENCES code_master(code) ON DELETE RESTRICT,
    title VARCHAR(500) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_document_group_person_type
    ON document_group (person_id, document_type_code) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_document_group_updated_at
BEFORE UPDATE ON document_group
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE document (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_group_id UUID NOT NULL REFERENCES document_group(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    is_latest BOOLEAN NOT NULL DEFAULT FALSE,
    document_date DATE,
    original_filename VARCHAR(500) NOT NULL,
    extension VARCHAR(30),
    mime_type VARCHAR(200),
    file_size BIGINT NOT NULL CHECK (file_size >= 0),
    storage_key TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    preview_storage_key TEXT,
    preview_page_count INTEGER CHECK (preview_page_count IS NULL OR preview_page_count >= 0),
    processing_status VARCHAR(30) NOT NULL DEFAULT 'UPLOADED'
        CHECK (processing_status IN ('UPLOADED', 'PROCESSING', 'READY', 'FAILED')),
    processing_error TEXT,
    uploaded_by UUID REFERENCES app_user(id) ON DELETE SET NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (document_group_id, version_no)
);

CREATE UNIQUE INDEX uq_document_group_latest
    ON document (document_group_id)
    WHERE is_latest = TRUE AND deleted_at IS NULL;
CREATE INDEX idx_document_sha256 ON document (sha256);
CREATE INDEX idx_document_group_version
    ON document (document_group_id, version_no DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_document_processing_status
    ON document (processing_status, uploaded_at DESC) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_document_updated_at
BEFORE UPDATE ON document
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE document_page (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL CHECK (page_no > 0),
    extracted_text TEXT,
    layout_json JSONB,
    extraction_method VARCHAR(30)
        CHECK (extraction_method IS NULL OR extraction_method IN ('TEXT_PARSER', 'VLM', 'OCR', 'HYBRID')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, page_no)
);

CREATE INDEX idx_document_page_document ON document_page (document_id, page_no);

CREATE TABLE document_chunk (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    page_from INTEGER,
    page_to INTEGER,
    chunk_text TEXT NOT NULL,
    token_count INTEGER CHECK (token_count IS NULL OR token_count >= 0),
    chunk_hash CHAR(64),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index),
    CHECK (page_to IS NULL OR page_from IS NULL OR page_to >= page_from)
);

CREATE INDEX idx_document_chunk_document ON document_chunk (document_id, chunk_index);

-- -----------------------------------------------------------------------------
-- AI analysis / review
-- -----------------------------------------------------------------------------

CREATE TABLE analysis_run (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'PROCESSING', 'REVIEWING', 'CONFIRMED', 'FAILED', 'CANCELLED')),
    candidate_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    base_profile_version INTEGER,
    llm_model VARCHAR(200),
    vlm_model VARCHAR(200),
    prompt_version VARCHAR(100),
    schema_version VARCHAR(100),
    overall_confidence NUMERIC(5,4)
        CHECK (overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    confirmed_by UUID REFERENCES app_user(id) ON DELETE SET NULL,
    confirmed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analysis_run_person_created
    ON analysis_run (person_id, created_at DESC);
CREATE INDEX idx_analysis_run_status_created
    ON analysis_run (status, created_at DESC);

CREATE TRIGGER trg_analysis_run_updated_at
BEFORE UPDATE ON analysis_run
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- add FK after analysis_run exists to avoid table creation cycle
ALTER TABLE project
    ADD CONSTRAINT fk_project_source_analysis_run
    FOREIGN KEY (source_analysis_run_id)
    REFERENCES analysis_run(id)
    ON DELETE SET NULL;

CREATE TABLE analysis_run_document (
    analysis_run_id UUID NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    analysis_role VARCHAR(50),
    PRIMARY KEY (analysis_run_id, document_id)
);

CREATE INDEX idx_analysis_run_document_document
    ON analysis_run_document (document_id, analysis_run_id);

CREATE TABLE analysis_diff_item (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
    entity_type VARCHAR(40) NOT NULL,
    candidate_path TEXT,
    existing_target_id UUID,
    field_name VARCHAR(200),
    change_type VARCHAR(20) NOT NULL
        CHECK (change_type IN ('SAME', 'NEW', 'UPDATE', 'CONFLICT', 'REVIEW')),
    old_value JSONB,
    new_value JSONB,
    confidence NUMERIC(5,4)
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_type VARCHAR(20)
        CHECK (evidence_type IS NULL OR evidence_type IN ('EXPLICIT', 'INFERRED')),
    review_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'MODIFIED', 'MERGED')),
    decided_value JSONB,
    decided_by UUID REFERENCES app_user(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analysis_diff_run_status
    ON analysis_diff_item (analysis_run_id, review_status);
CREATE INDEX idx_analysis_diff_run_change
    ON analysis_diff_item (analysis_run_id, change_type);

-- -----------------------------------------------------------------------------
-- Evidence
-- -----------------------------------------------------------------------------

CREATE TABLE evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    document_page_id UUID REFERENCES document_page(id) ON DELETE SET NULL,
    page_no INTEGER,
    quote_text TEXT,
    bbox_json JSONB,
    char_start INTEGER,
    char_end INTEGER,
    extraction_method VARCHAR(30)
        CHECK (extraction_method IS NULL OR extraction_method IN ('TEXT_PARSER', 'VLM', 'OCR', 'HYBRID')),
    confidence NUMERIC(5,4)
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (char_end IS NULL OR char_start IS NULL OR char_end >= char_start)
);

CREATE INDEX idx_evidence_document_page ON evidence (document_id, page_no);

CREATE TABLE analysis_diff_evidence (
    diff_item_id UUID NOT NULL REFERENCES analysis_diff_item(id) ON DELETE CASCADE,
    evidence_id UUID NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    PRIMARY KEY (diff_item_id, evidence_id)
);

CREATE INDEX idx_analysis_diff_evidence_evidence
    ON analysis_diff_evidence (evidence_id, diff_item_id);

CREATE TABLE evidence_link (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id UUID NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    target_type VARCHAR(50) NOT NULL,
    target_id UUID NOT NULL,
    field_name VARCHAR(200),
    relation_type VARCHAR(50) NOT NULL DEFAULT 'SUPPORTS',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_evidence_link_target
    ON evidence_link (target_type, target_id);
CREATE INDEX idx_evidence_link_evidence
    ON evidence_link (evidence_id);

-- -----------------------------------------------------------------------------
-- Revision / audit
-- -----------------------------------------------------------------------------

CREATE TABLE profile_revision (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
    snapshot_json JSONB NOT NULL,
    source_type VARCHAR(30) NOT NULL
        CHECK (source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION', 'SYSTEM')),
    source_analysis_run_id UUID REFERENCES analysis_run(id) ON DELETE SET NULL,
    created_by UUID REFERENCES app_user(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (person_id, revision_no)
);

CREATE INDEX idx_profile_revision_person
    ON profile_revision (person_id, revision_no DESC);

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES app_user(id) ON DELETE SET NULL,
    action_type VARCHAR(80) NOT NULL,
    target_type VARCHAR(50) NOT NULL,
    target_id UUID,
    before_json JSONB,
    after_json JSONB,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_target
    ON audit_log (target_type, target_id, created_at DESC);
CREATE INDEX idx_audit_log_user_created
    ON audit_log (user_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- Search / embedding index
-- -----------------------------------------------------------------------------

CREATE TABLE search_index_item (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    object_type VARCHAR(30) NOT NULL
        CHECK (object_type IN ('PROFILE', 'PROJECT', 'DOCUMENT_CHUNK')),
    object_id UUID NOT NULL,
    search_text TEXT NOT NULL,
    search_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', COALESCE(search_text, ''))
    ) STORED,
    embedding VECTOR(1024),
    source_weight NUMERIC(6,3) NOT NULL DEFAULT 1.000,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding_model VARCHAR(200),
    embedding_version VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_search_index_person_type
    ON search_index_item (person_id, object_type) WHERE is_active = TRUE;
CREATE INDEX idx_search_index_tsv
    ON search_index_item USING GIN (search_tsv) WHERE is_active = TRUE;
CREATE INDEX idx_search_index_text_trgm
    ON search_index_item USING GIN (search_text gin_trgm_ops) WHERE is_active = TRUE;
CREATE INDEX idx_search_index_embedding_hnsw
    ON search_index_item USING HNSW (embedding vector_cosine_ops)
    WHERE is_active = TRUE AND embedding IS NOT NULL;
CREATE UNIQUE INDEX uq_search_index_active_object_version
    ON search_index_item (
        object_type,
        object_id,
        COALESCE(embedding_model, ''),
        COALESCE(embedding_version, '')
    )
    WHERE is_active = TRUE;

CREATE TRIGGER trg_search_index_item_updated_at
BEFORE UPDATE ON search_index_item
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE search_index_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID REFERENCES person(id) ON DELETE CASCADE,
    object_type VARCHAR(30)
        CHECK (object_type IS NULL OR object_type IN ('PROFILE', 'PROJECT', 'DOCUMENT_CHUNK')),
    object_id UUID,
    action VARCHAR(20) NOT NULL
        CHECK (action IN ('UPSERT', 'DELETE', 'REBUILD_PERSON', 'REBUILD_ALL')),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    idempotency_key VARCHAR(300) UNIQUE,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_search_index_job_status_created
    ON search_index_job (status, created_at);
CREATE INDEX idx_search_index_job_person
    ON search_index_job (person_id, created_at DESC);
'''


def _ensure_extensions(conn) -> None:
    """Create required extensions without aborting the transaction on errors.

    Order:
    1) SELECT existence from pg_extension
    2) skip if present
    3) CREATE EXTENSION if missing
    4) on privilege failure raise a clear RuntimeError
    """
    for ext in REQUIRED_EXTENSIONS:
        exists = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = :name"),
            {"name": ext},
        ).scalar()
        if exists:
            continue
        try:
            conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Extension '{ext}' is missing and could not be created ({exc}). "
                "Create it as a PostgreSQL superuser first, e.g.\n"
                f'  CREATE EXTENSION IF NOT EXISTS "{ext}";'
            ) from exc


def upgrade() -> None:
    conn = op.get_bind()

    # Skip if baseline tables already exist (e.g. Cloud Agent applied schema.sql).
    existing = conn.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "AND table_name = 'app_user'"
        )
    ).scalar()
    if existing and int(existing) > 0:
        return

    _ensure_extensions(conn)

    raw_conn = conn.connection.dbapi_connection
    raw_conn.execute(BASELINE_DDL)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            DROP VIEW IF EXISTS vw_person_customer_type CASCADE;
            DROP VIEW IF EXISTS vw_person_business_domain CASCADE;
            DROP TABLE IF EXISTS search_index_job CASCADE;
            DROP TABLE IF EXISTS search_index_item CASCADE;
            DROP TABLE IF EXISTS audit_log CASCADE;
            DROP TABLE IF EXISTS profile_revision CASCADE;
            DROP TABLE IF EXISTS evidence_link CASCADE;
            DROP TABLE IF EXISTS analysis_diff_evidence CASCADE;
            DROP TABLE IF EXISTS evidence CASCADE;
            DROP TABLE IF EXISTS analysis_diff_item CASCADE;
            DROP TABLE IF EXISTS analysis_run_document CASCADE;
            DROP TABLE IF EXISTS analysis_run CASCADE;
            DROP TABLE IF EXISTS document_chunk CASCADE;
            DROP TABLE IF EXISTS document_page CASCADE;
            DROP TABLE IF EXISTS document CASCADE;
            DROP TABLE IF EXISTS document_group CASCADE;
            DROP TABLE IF EXISTS project_customer_type CASCADE;
            DROP TABLE IF EXISTS project_business_domain CASCADE;
            DROP TABLE IF EXISTS project_expertise CASCADE;
            DROP TABLE IF EXISTS project_skill CASCADE;
            DROP TABLE IF EXISTS project_job CASCADE;
            DROP TABLE IF EXISTS project CASCADE;
            DROP TABLE IF EXISTS certification CASCADE;
            DROP TABLE IF EXISTS education CASCADE;
            DROP TABLE IF EXISTS employment_history CASCADE;
            DROP TABLE IF EXISTS person_expertise CASCADE;
            DROP TABLE IF EXISTS person_skill CASCADE;
            DROP TABLE IF EXISTS person_job CASCADE;
            DROP TABLE IF EXISTS upload_temp_file CASCADE;
            DROP TABLE IF EXISTS upload_session CASCADE;
            DROP TABLE IF EXISTS person_profile CASCADE;
            DROP TABLE IF EXISTS person CASCADE;
            DROP TABLE IF EXISTS code_alias CASCADE;
            DROP TABLE IF EXISTS code_master CASCADE;
            DROP TABLE IF EXISTS app_user CASCADE;
            DROP FUNCTION IF EXISTS set_updated_at() CASCADE;
            """
        )
    )
