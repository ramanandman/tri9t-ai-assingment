"""
SQLAlchemy models.

Design notes (see APPROACH.md for the full write-up):

- Document: one row per logical document ("the CT-200 manual"). This
  row never changes once created.

- DocumentVersion: one row per ingestion (v1, v2, ...). Each version
  belongs to a Document. Ingesting v2 does NOT touch v1's row or any
  of v1's Nodes - it just adds a new DocumentVersion and a fresh set
  of Nodes tied to it. This is what lets old Selections keep resolving
  to the exact v1 text even after v2 exists.

- Node: one row per tree node (a heading + its body). Every Node
  belongs to exactly one DocumentVersion (nodes are NOT shared across
  versions as mutable rows - "the same logical section" in v1 and v2
  are two separate Node rows, matched via `numbering` at query time,
  not the same database row edited in place). This is a deliberate
  simplicity-over-cleverness choice: it makes "does this node's content
  differ between versions" a simple two-row comparison instead of a
  mutable-history/audit-log design, at the cost of not having a single
  stable "logical node ID" that spans versions - we treat `numbering`
  as that logical identity instead, and document its failure mode
  (renumbering an section between versions would break the match) in
  APPROACH.md.

- Selection / SelectionNode: a named set of node+version pins. Storing
  the version_id alongside each node_id (not just relying on the node
  row's own version_id) is technically redundant since a Node only ever
  belongs to one version - but it's kept explicit here for clarity and
  because it is exactly what the assignment asks selections to record.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    ForeignKey,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    versions = relationship("DocumentVersion", back_populates="document", order_by="DocumentVersion.version_number")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(String, primary_key=True, default=_uuid)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    version_number = Column(Integer, nullable=False)  # 1, 2, 3...
    source_filename = Column(String, nullable=False)
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="versions")
    nodes = relationship("Node", back_populates="version", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
    )


class Node(Base):
    __tablename__ = "nodes"

    id = Column(String, primary_key=True, default=_uuid)
    version_id = Column(String, ForeignKey("document_versions.id"), nullable=False)
    parent_id = Column(String, ForeignKey("nodes.id"), nullable=True)

    numbering = Column(String, nullable=False)  # e.g. "2.1.1.1", "" for synthetic root
    heading = Column(String, nullable=False)
    level = Column(Integer, nullable=False)
    body_text = Column(Text, nullable=False, default="")
    content_hash = Column(String, nullable=False)
    order_index = Column(Integer, nullable=False)

    version = relationship("DocumentVersion", back_populates="nodes")
    children = relationship("Node", backref="parent", remote_side=[id])

    def to_dict(self, include_children: bool = False):
        d = {
            "id": self.id,
            "version_id": self.version_id,
            "parent_id": self.parent_id,
            "numbering": self.numbering,
            "heading": self.heading,
            "level": self.level,
            "body_text": self.body_text,
            "content_hash": self.content_hash,
            "order_index": self.order_index,
        }
        if include_children:
            d["children"] = [c.to_dict(include_children=True) for c in sorted(self.children, key=lambda c: c.order_index)]
        return d


class Selection(Base):
    __tablename__ = "selections"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    items = relationship("SelectionNode", back_populates="selection", cascade="all, delete-orphan")


class SelectionNode(Base):
    """
    A single (node, version) pin within a Selection.
    version_id is stored explicitly (even though it's derivable from the
    node row) because the assignment explicitly asks selections to
    record the version they were made against - being explicit here
    makes that requirement visible in the schema itself, not just
    implied by a join.
    """
    __tablename__ = "selection_nodes"

    id = Column(String, primary_key=True, default=_uuid)
    selection_id = Column(String, ForeignKey("selections.id"), nullable=False)
    node_id = Column(String, ForeignKey("nodes.id"), nullable=False)
    version_id = Column(String, ForeignKey("document_versions.id"), nullable=False)

    selection = relationship("Selection", back_populates="items")
    node = relationship("Node")
