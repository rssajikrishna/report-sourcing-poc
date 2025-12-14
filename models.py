# models.py (patched)
import os
import datetime
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    JSON,
    UniqueConstraint,
    Float,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = os.path.dirname(__file__)
DB_DIR = os.path.join(BASE_DIR, "db")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "reports.db")

DATABASE_URL = f"sqlite:///{DB_PATH}"
# allow multithreaded access for simple scripts; tune for production if needed
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
# keep objects usable after commit (convenient for scripts/REPL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    domain = Column(String, nullable=True)
    investor_url = Column(String, nullable=True)
    edgar_cik = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    # relationship: one company -> many documents
    documents = relationship("Document", back_populates="company", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self):
        return f"<Company id={self.id!r} name={self.name!r}>"


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    filename = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)
    source_url = Column(String, nullable=False, index=True)
    pdf_url = Column(String, nullable=True)
    sha256 = Column(String, index=True, nullable=False)
    document_type = Column(String, nullable=True)   # Annual, Q1, Q2, Q3, Q4, Half-Yearly, Unknown
    fiscal_year = Column(Integer, nullable=True)
    published_date = Column(String, nullable=True)
    pages = Column(Integer, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    detection_method = Column(String, nullable=True)  # serp, sitemap, probe_path, discovery_crawl, edgar, manual
    confidence = Column(Float, nullable=True)
    evidence_snippet = Column(Text, nullable=True)
    extra_metadata = Column(JSON, nullable=True)   # renamed from 'metadata' to avoid collision
    review_status = Column(String, default="pending", nullable=False)  # pending/accepted/rejected
    ingested_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    # relationship back to company
    company = relationship("Company", back_populates="documents", lazy="joined")

    __table_args__ = (
        UniqueConstraint("sha256", name="uix_sha256"),
        Index("ix_documents_company_filename", "company_id", "filename"),
    )

    def __repr__(self):
        return f"<Document id={self.id!r} filename={self.filename!r} sha256={self.sha256[:8] if self.sha256 else None!r}>"


def init_db():
    """Create DB file and tables if not present."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("DB initialized at:", DB_PATH)
