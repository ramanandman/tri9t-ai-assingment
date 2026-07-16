"""
Node-level routes:
  GET /nodes/{node_id}          - a single node with children, full text, hash
  GET /search?q=...             - search headings/body text across a version
  GET /nodes/{node_id}/changes  - has this node changed across versions, and how
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Node, DocumentVersion
from app.schemas import NodeDetail, NodeSummary, NodeChangeResponse
from app.services.versioning import diff_versions

router = APIRouter()


@router.get("/nodes/{node_id}", response_model=NodeDetail)
def get_node(node_id: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return NodeDetail(**node.to_dict(include_children=True))


@router.get("/search", response_model=list[NodeSummary])
def search_nodes(
    q: str = Query(..., min_length=1, description="Search term for heading or body text"),
    version_id: str | None = Query(None, description="Restrict search to a specific version"),
    db: Session = Depends(get_db),
):
    """
    Case-insensitive substring search across heading and body text.
    If version_id is not given, searches across ALL versions (results
    may include the same logical section from multiple versions).
    """
    query = db.query(Node)
    if version_id:
        query = query.filter(Node.version_id == version_id)

    like_pattern = f"%{q}%"
    results = query.filter(
        (Node.heading.ilike(like_pattern)) | (Node.body_text.ilike(like_pattern))
    ).order_by(Node.order_index).all()

    return results


@router.get("/nodes/{node_id}/changes", response_model=NodeChangeResponse)
def get_node_changes(node_id: str, db: Session = Depends(get_db)):
    """
    Given a node ID, determine whether it changed relative to the
    PREVIOUS version of the same document (by matching on numbering).
    If this node is in version 1 (no earlier version exists), or if
    there's no matching numbering in the adjacent version, we report
    that explicitly rather than guessing.
    """
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

    this_version = db.query(DocumentVersion).filter(DocumentVersion.id == node.version_id).first()

    prior_version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == this_version.document_id,
            DocumentVersion.version_number < this_version.version_number,
        )
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    if not prior_version:
        return NodeChangeResponse(
            numbering=node.numbering,
            status="unchanged",
            old_node_id=None,
            new_node_id=node.id,
            summary="This is the first version of the document; there is no prior version to compare against.",
        )

    changes = diff_versions(db, prior_version.id, this_version.id)
    match = next((c for c in changes if c.numbering == node.numbering), None)

    if not match:
        raise HTTPException(
            status_code=500,
            detail=f"Internal inconsistency: node {node_id} not found in diff results",
        )

    return NodeChangeResponse(
        numbering=match.numbering,
        status=match.status,
        old_node_id=match.old_node_id,
        new_node_id=match.new_node_id,
        summary=match.summary,
    )
