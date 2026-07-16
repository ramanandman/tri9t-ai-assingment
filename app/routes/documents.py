"""
Document-level routes:
  GET  /documents/{document_id}/sections   - top-level sections, version-aware
  POST /documents/{document_id}/versions   - ingest a new version (e.g. v2) from a PDF path
  GET  /documents/{document_id}/versions   - list versions of a document
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, DocumentVersion, Node
from app.schemas import NodeSummary, DocumentVersionSummary, IngestVersionRequest
from app.services.versioning import ingest_version

router = APIRouter()


@router.post("/documents")
def create_document(name: str, db: Session = Depends(get_db)):
    """
    Create a new (empty) Document record. Call this once, then use the
    returned id with POST /documents/{id}/versions to ingest v1, v2, etc.
    """
    doc = Document(name=name)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "name": doc.name}


@router.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).all()
    return [{"id": d.id, "name": d.name} for d in docs]


def _get_document_or_404(db: Session, document_id: str) -> Document:
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return doc


def _resolve_version(db: Session, document_id: str, version_number: int | None) -> DocumentVersion:
    """
    version_number=None means "latest". Otherwise fetch that exact
    version number for this document.
    """
    query = db.query(DocumentVersion).filter(DocumentVersion.document_id == document_id)
    if version_number is None:
        version = query.order_by(DocumentVersion.version_number.desc()).first()
    else:
        version = query.filter(DocumentVersion.version_number == version_number).first()
    if not version:
        raise HTTPException(
            status_code=404,
            detail=f"No version {version_number if version_number else '(latest)'} found for document {document_id}",
        )
    return version


@router.get("/documents/{document_id}/sections", response_model=list[NodeSummary])
def list_top_level_sections(
    document_id: str,
    version: int | None = None,
    db: Session = Depends(get_db),
):
    """
    List top-level sections (level=1 nodes) for a document.
    `version` is optional; defaults to the latest ingested version.
    """
    _get_document_or_404(db, document_id)
    doc_version = _resolve_version(db, document_id, version)

    sections = (
        db.query(Node)
        .filter(Node.version_id == doc_version.id, Node.level == 1)
        .order_by(Node.order_index)
        .all()
    )
    return sections


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersionSummary])
def list_versions(document_id: str, db: Session = Depends(get_db)):
    _get_document_or_404(db, document_id)
    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
        .all()
    )
    return versions


@router.post("/documents/{document_id}/versions", response_model=DocumentVersionSummary)
def create_version(document_id: str, request: IngestVersionRequest, db: Session = Depends(get_db)):
    """
    Ingest a new version of this document from a PDF file path on disk
    (e.g. "data/ct200_manual_v2.pdf"). This is how the v1 -> v2
    re-ingestion flow is triggered via the API.
    """
    _get_document_or_404(db, document_id)
    source_filename = request.source_filename or request.pdf_path.split("/")[-1]
    version = ingest_version(db, document_id, request.pdf_path, source_filename)
    return version
