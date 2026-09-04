"""ORM models matching db/schema.sql (MVP baseline).

Operational schema changes after bootstrap must go through Alembic.
"""

from app.db.models.user import AppUser
from app.db.models.code import CodeAlias, CodeMaster
from app.db.models.person import (
    Certification,
    Education,
    EmploymentHistory,
    Person,
    PersonExpertise,
    PersonJob,
    PersonProfile,
    PersonSkill,
)
from app.db.models.upload import UploadSession, UploadTempFile
from app.db.models.project import (
    Project,
    ProjectBusinessDomain,
    ProjectCustomerType,
    ProjectExpertise,
    ProjectJob,
    ProjectSkill,
)
from app.db.models.document import Document, DocumentChunk, DocumentGroup, DocumentPage
from app.db.models.analysis import (
    AnalysisDiffEvidence,
    AnalysisDiffItem,
    AnalysisRun,
    AnalysisRunDocument,
)
from app.db.models.evidence import Evidence, EvidenceLink
from app.db.models.revision import AuditLog, ProfileRevision
from app.db.models.search import SearchIndexItem, SearchIndexJob

__all__ = [
    "AppUser",
    "CodeMaster",
    "CodeAlias",
    "Person",
    "PersonProfile",
    "PersonJob",
    "PersonSkill",
    "PersonExpertise",
    "EmploymentHistory",
    "Education",
    "Certification",
    "UploadSession",
    "UploadTempFile",
    "Project",
    "ProjectJob",
    "ProjectSkill",
    "ProjectExpertise",
    "ProjectBusinessDomain",
    "ProjectCustomerType",
    "DocumentGroup",
    "Document",
    "DocumentPage",
    "DocumentChunk",
    "AnalysisRun",
    "AnalysisRunDocument",
    "AnalysisDiffItem",
    "AnalysisDiffEvidence",
    "Evidence",
    "EvidenceLink",
    "ProfileRevision",
    "AuditLog",
    "SearchIndexItem",
    "SearchIndexJob",
]
