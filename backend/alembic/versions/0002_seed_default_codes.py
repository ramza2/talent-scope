"""Seed default TalentScope reference codes.

Revision ID: 0002_seed_default_codes
Revises: 0001_initial_schema
Create Date: 2026-09-17

The MVP UI and analysis pipeline require a minimal canonical code catalog on a
fresh database. This revision seeds only codes that are explicitly defined in
TalentScope design/API documents or already used as canonical references by the
server test suite.

Existing rows are never overwritten. Operators may edit seeded rows later via
the code-management UI without a future deploy resetting those changes.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0002_seed_default_codes"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


ROOT_CODES = (
    # DOC_TYPE — docs/05_document_management.md
    {"code": "DOC-RESUME", "code_type": "DOC_TYPE", "name": "이력서", "parent_code": None, "sort_order": 10},
    {"code": "DOC-PROFILE", "code_type": "DOC_TYPE", "name": "인력 프로필", "parent_code": None, "sort_order": 20},
    {"code": "DOC-CAREER", "code_type": "DOC_TYPE", "name": "경력기술서", "parent_code": None, "sort_order": 30},
    {"code": "DOC-KOSA", "code_type": "DOC_TYPE", "name": "KOSA 등 경력증명", "parent_code": None, "sort_order": 40},
    {"code": "DOC-CERT", "code_type": "DOC_TYPE", "name": "자격증/인증서", "parent_code": None, "sort_order": 50},
    {"code": "DOC-PORTFOLIO", "code_type": "DOC_TYPE", "name": "포트폴리오", "parent_code": None, "sort_order": 60},
    {"code": "DOC-EDU", "code_type": "DOC_TYPE", "name": "학력/교육 증빙", "parent_code": None, "sort_order": 70},
    {"code": "DOC-OTHER", "code_type": "DOC_TYPE", "name": "기타", "parent_code": None, "sort_order": 80},

    # JOB roots / standalone codes — docs/04_code_taxonomy.md
    {"code": "JOB-MGT", "code_type": "JOB", "name": "사업관리", "parent_code": None, "sort_order": 100},
    {"code": "JOB-ARC", "code_type": "JOB", "name": "아키텍처", "parent_code": None, "sort_order": 200},
    {"code": "JOB-DATA", "code_type": "JOB", "name": "데이터/DB", "parent_code": None, "sort_order": 300},
    {"code": "JOB-DEV", "code_type": "JOB", "name": "개발", "parent_code": None, "sort_order": 400},
    {"code": "JOB-AI", "code_type": "JOB", "name": "AI", "parent_code": None, "sort_order": 500},
    {"code": "JOB-SYS", "code_type": "JOB", "name": "시스템", "parent_code": None, "sort_order": 600},
    {"code": "JOB-NET-ENG", "code_type": "JOB", "name": "네트워크엔지니어", "parent_code": None, "sort_order": 700},
    {"code": "JOB-SEC-ENG", "code_type": "JOB", "name": "보안엔지니어", "parent_code": None, "sort_order": 710},
    {"code": "JOB-OPS-SYS", "code_type": "JOB", "name": "시스템운영", "parent_code": None, "sort_order": 720},
    {"code": "JOB-OPS-APP", "code_type": "JOB", "name": "애플리케이션운영", "parent_code": None, "sort_order": 730},
    {"code": "JOB-QA-ENG", "code_type": "JOB", "name": "QA", "parent_code": None, "sort_order": 740},

    # TECH category roots explicitly named in docs/04_code_taxonomy.md
    {"code": "TECH-LANG", "code_type": "TECH", "name": "Language", "parent_code": None, "sort_order": 100},
    {"code": "TECH-BE", "code_type": "TECH", "name": "Backend", "parent_code": None, "sort_order": 200},
    {"code": "TECH-FE", "code_type": "TECH", "name": "Frontend", "parent_code": None, "sort_order": 300},
    {"code": "TECH-DB", "code_type": "TECH", "name": "Database", "parent_code": None, "sort_order": 400},
    {"code": "TECH-AI", "code_type": "TECH", "name": "AI/ML", "parent_code": None, "sort_order": 500},
    {"code": "TECH-INFRA", "code_type": "TECH", "name": "Infra/DevOps", "parent_code": None, "sort_order": 600},
    {"code": "TECH-DATA", "code_type": "TECH", "name": "Data", "parent_code": None, "sort_order": 700},

    # EXP category roots explicitly named in docs/04_code_taxonomy.md
    {"code": "EXP-AI", "code_type": "EXP", "name": "AI", "parent_code": None, "sort_order": 100},
    {"code": "EXP-SW", "code_type": "EXP", "name": "SW", "parent_code": None, "sort_order": 200},
    {"code": "EXP-DATA", "code_type": "EXP", "name": "Data/DB", "parent_code": None, "sort_order": 300},
    {"code": "EXP-INFRA", "code_type": "EXP", "name": "Infra", "parent_code": None, "sort_order": 400},
    {"code": "EXP-MGT", "code_type": "EXP", "name": "Management", "parent_code": None, "sort_order": 500},

    # BIZ canonical codes — docs/04_code_taxonomy.md
    {"code": "BIZ-PUBLIC", "code_type": "BIZ", "name": "공공", "parent_code": None, "sort_order": 100},
    {"code": "BIZ-DEFENSE", "code_type": "BIZ", "name": "국방", "parent_code": None, "sort_order": 200},
    {"code": "BIZ-HEALTHCARE", "code_type": "BIZ", "name": "의료/헬스케어", "parent_code": None, "sort_order": 300},
    {"code": "BIZ-FINANCE", "code_type": "BIZ", "name": "금융", "parent_code": None, "sort_order": 400},
    {"code": "BIZ-MANUFACTURING", "code_type": "BIZ", "name": "제조", "parent_code": None, "sort_order": 500},
    {"code": "BIZ-ENERGY", "code_type": "BIZ", "name": "에너지", "parent_code": None, "sort_order": 600},
    {"code": "BIZ-TELECOM", "code_type": "BIZ", "name": "통신", "parent_code": None, "sort_order": 700},
    {"code": "BIZ-RETAIL", "code_type": "BIZ", "name": "유통", "parent_code": None, "sort_order": 800},
    {"code": "BIZ-LOGISTICS", "code_type": "BIZ", "name": "물류", "parent_code": None, "sort_order": 900},
    {"code": "BIZ-EDUCATION", "code_type": "BIZ", "name": "교육", "parent_code": None, "sort_order": 1000},
    {"code": "BIZ-TRANSPORT", "code_type": "BIZ", "name": "교통", "parent_code": None, "sort_order": 1100},
    {"code": "BIZ-CONSTRUCTION", "code_type": "BIZ", "name": "건설", "parent_code": None, "sort_order": 1200},
    {"code": "BIZ-MEDIA", "code_type": "BIZ", "name": "미디어", "parent_code": None, "sort_order": 1300},
    {"code": "BIZ-ENTERPRISE", "code_type": "BIZ", "name": "일반기업", "parent_code": None, "sort_order": 1400},
)


CHILD_CODES = (
    # JOB children — docs/04_code_taxonomy.md
    {"code": "JOB-MGT-PM", "code_type": "JOB", "name": "PM", "parent_code": "JOB-MGT", "sort_order": 110},
    {"code": "JOB-MGT-PL", "code_type": "JOB", "name": "PL", "parent_code": "JOB-MGT", "sort_order": 120},
    {"code": "JOB-MGT-PMO", "code_type": "JOB", "name": "PMO", "parent_code": "JOB-MGT", "sort_order": 130},
    {"code": "JOB-ARC-SA", "code_type": "JOB", "name": "SA", "parent_code": "JOB-ARC", "sort_order": 210},
    {"code": "JOB-ARC-AA", "code_type": "JOB", "name": "AA", "parent_code": "JOB-ARC", "sort_order": 220},
    {"code": "JOB-ARC-TA", "code_type": "JOB", "name": "TA", "parent_code": "JOB-ARC", "sort_order": 230},
    {"code": "JOB-DATA-DA", "code_type": "JOB", "name": "DA", "parent_code": "JOB-DATA", "sort_order": 310},
    {"code": "JOB-DATA-DBA", "code_type": "JOB", "name": "DBA", "parent_code": "JOB-DATA", "sort_order": 320},
    {"code": "JOB-DEV-GEN", "code_type": "JOB", "name": "개발자", "parent_code": "JOB-DEV", "sort_order": 410},
    {"code": "JOB-DEV-BE", "code_type": "JOB", "name": "백엔드개발자", "parent_code": "JOB-DEV", "sort_order": 420},
    {"code": "JOB-DEV-FE", "code_type": "JOB", "name": "프론트엔드개발자", "parent_code": "JOB-DEV", "sort_order": 430},
    {"code": "JOB-DEV-FS", "code_type": "JOB", "name": "풀스택개발자", "parent_code": "JOB-DEV", "sort_order": 440},
    {"code": "JOB-DEV-MOB", "code_type": "JOB", "name": "모바일개발자", "parent_code": "JOB-DEV", "sort_order": 450},
    {"code": "JOB-DEV-INT", "code_type": "JOB", "name": "인터페이스개발자", "parent_code": "JOB-DEV", "sort_order": 460},
    {"code": "JOB-AI-DEV", "code_type": "JOB", "name": "AI개발자", "parent_code": "JOB-AI", "sort_order": 510},
    {"code": "JOB-AI-ML", "code_type": "JOB", "name": "ML Engineer", "parent_code": "JOB-AI", "sort_order": 520},
    {"code": "JOB-AI-LLM", "code_type": "JOB", "name": "LLM Engineer", "parent_code": "JOB-AI", "sort_order": 530},
    {"code": "JOB-AI-VISION", "code_type": "JOB", "name": "Vision AI Engineer", "parent_code": "JOB-AI", "sort_order": 540},
    {"code": "JOB-AI-PLATFORM", "code_type": "JOB", "name": "AI Platform Engineer", "parent_code": "JOB-AI", "sort_order": 550},
    {"code": "JOB-SYS-SE", "code_type": "JOB", "name": "시스템SE", "parent_code": "JOB-SYS", "sort_order": 610},
    {"code": "JOB-SYS-OS", "code_type": "JOB", "name": "Linux/Unix SE", "parent_code": "JOB-SYS", "sort_order": 620},
    {"code": "JOB-SYS-CLOUD", "code_type": "JOB", "name": "Cloud Engineer", "parent_code": "JOB-SYS", "sort_order": 630},
    {"code": "JOB-SYS-MW", "code_type": "JOB", "name": "Middleware Engineer", "parent_code": "JOB-SYS", "sort_order": 640},

    # Canonical leaf examples already fixed by docs/API/tests.
    {"code": "TECH-LANG-PYTHON", "code_type": "TECH", "name": "Python", "parent_code": "TECH-LANG", "sort_order": 110},
    {"code": "TECH-DB-ORACLE", "code_type": "TECH", "name": "Oracle", "parent_code": "TECH-DB", "sort_order": 410},
    {"code": "EXP-AI-RAG", "code_type": "EXP", "name": "RAG", "parent_code": "EXP-AI", "sort_order": 110},
    {"code": "EXP-DATA-DB-TUNING", "code_type": "EXP", "name": "DB 튜닝", "parent_code": "EXP-DATA", "sort_order": 310},
)


_INSERT_SQL = text(
    """
    INSERT INTO code_master (
        code,
        code_type,
        parent_code,
        name,
        sort_order,
        is_active
    ) VALUES (
        :code,
        :code_type,
        :parent_code,
        :name,
        :sort_order,
        TRUE
    )
    ON CONFLICT (code) DO NOTHING
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(_INSERT_SQL, list(ROOT_CODES))
    bind.execute(_INSERT_SQL, list(CHILD_CODES))


def downgrade() -> None:
    # Reference data is intentionally retained on downgrade. Removing a seeded
    # code can be destructive once it is referenced by profiles/documents, and
    # re-upgrade is safe because upgrade() uses ON CONFLICT DO NOTHING.
    pass
