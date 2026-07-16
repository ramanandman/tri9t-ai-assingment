"""
Versioning service.

Handles:
1. Ingesting a new DocumentVersion (parsing a PDF and saving its nodes).
2. Matching nodes between two versions of the same document.
3. Producing a change summary (unchanged / changed / added / removed).

Version-matching strategy (documented here, and in APPROACH.md):

We match nodes ACROSS versions by their `numbering` field (e.g. "3.3",
"2.1.1.1"), NOT by heading text and NOT by a stable database ID that
persists across ingests. We chose this because:

- Heading text is not reliable: the manual has two different nodes
  both literally named "Error Codes" (4.2 and 7.1). Matching by text
  would incorrectly treat every "Error Codes" node across the whole
  document as "the same node."
- `numbering` is the closest thing this document has to a stable
  "requirement ID" - it's how a human would refer to the section
  ("see section 3.3") - and it re-appears in the assignment's own
  cross-reference text ("see 2.1, 4.3").

Known failure mode (deliberately NOT hidden): if a section is
renumbered between versions - e.g. an editor inserts a new section 3.3
and pushes the old 3.3 down to become 3.4 - this matcher will report
the old section as REMOVED and the shifted section as ADDED, even
though a human would recognize it as the same content that just moved.
Detecting that would need fuzzy title/body similarity matching across
different numbering, which we deliberately did NOT build (see
APPROACH.md "what I'd do with more time") because the assignment scope
prioritizes correctness on the concrete cases in this manual over a
generic renumbering-robust matcher.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Document, DocumentVersion, Node
from app.parser.pdf_parser import build_tree, flatten


@dataclass
class NodeChange:
    numbering: str
    status: str  # "unchanged" | "changed" | "added" | "removed"
    old_node_id: str | None
    new_node_id: str | None
    summary: str


def ingest_version(db: Session, document_id: str, pdf_path: str, source_filename: str) -> DocumentVersion:
    """
    Parse the given PDF and save it as a new DocumentVersion under the
    given document. Does NOT touch any existing version's nodes.
    """
    existing_versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .all()
    )
    next_version_number = (existing_versions[0].version_number + 1) if existing_versions else 1

    version = DocumentVersion(
        document_id=document_id,
        version_number=next_version_number,
        source_filename=source_filename,
    )
    db.add(version)
    db.flush()

    tree = build_tree(pdf_path, document_id=f"v{next_version_number}")
    parsed_nodes = flatten(tree)

    id_map = {}
    db_nodes = []
    for pn in parsed_nodes:
        db_node = Node(
            version_id=version.id,
            numbering=pn.numbering,
            heading=pn.heading,
            level=pn.level,
            body_text=pn.body_text,
            content_hash=pn.content_hash,
            order_index=pn.order_index,
            parent_id=None,
        )
        db.add(db_node)
        db.flush()
        id_map[pn.id] = db_node.id
        db_nodes.append((pn, db_node))

    for pn, db_node in db_nodes:
        if pn.parent_id:
            db_node.parent_id = id_map[pn.parent_id]

    db.commit()
    return version


def diff_versions(db: Session, old_version_id: str, new_version_id: str) -> list[NodeChange]:
    """
    Compare two versions node-by-node, matched on `numbering`.
    Returns a list of NodeChange, one per numbering seen in EITHER
    version (so additions and removals are both represented).
    """
    old_nodes = {
        n.numbering: n
        for n in db.query(Node).filter(Node.version_id == old_version_id).all()
        if n.numbering  # skip the synthetic root, which has numbering=""
    }
    new_nodes = {
        n.numbering: n
        for n in db.query(Node).filter(Node.version_id == new_version_id).all()
        if n.numbering
    }

    all_numberings = sorted(set(old_nodes) | set(new_nodes), key=_sort_key)
    changes = []

    for numbering in all_numberings:
        old_node = old_nodes.get(numbering)
        new_node = new_nodes.get(numbering)

        if old_node and not new_node:
            changes.append(NodeChange(
                numbering=numbering, status="removed",
                old_node_id=old_node.id, new_node_id=None,
                summary=f"Section {numbering} ({old_node.heading!r}) existed in the old version but is gone in the new version.",
            ))
        elif new_node and not old_node:
            changes.append(NodeChange(
                numbering=numbering, status="added",
                old_node_id=None, new_node_id=new_node.id,
                summary=f"Section {numbering} ({new_node.heading!r}) is new in this version.",
            ))
        elif old_node.content_hash == new_node.content_hash:
            changes.append(NodeChange(
                numbering=numbering, status="unchanged",
                old_node_id=old_node.id, new_node_id=new_node.id,
                summary="No change.",
            ))
        else:
            changes.append(NodeChange(
                numbering=numbering, status="changed",
                old_node_id=old_node.id, new_node_id=new_node.id,
                summary=_summarize_change(old_node, new_node),
            ))

    return changes


def _summarize_change(old_node: Node, new_node: Node) -> str:
    """
    Lightweight diff summary. We deliberately do NOT attempt a full
    word-level diff here (out of scope / not required) - we report
    WHICH parts differ (heading vs body) and the before/after body
    length as a cheap signal of how big the change was. A human (or
    the LLM-generation step) can always look at old_node/new_node's
    full body_text directly for the real detail.
    """
    parts = []
    if old_node.heading != new_node.heading:
        parts.append(f"heading changed from {old_node.heading!r} to {new_node.heading!r}")
    if old_node.body_text != new_node.body_text:
        parts.append(
            f"body text changed ({len(old_node.body_text)} -> {len(new_node.body_text)} chars)"
        )
    if not parts:
        parts.append("content hash differs but heading/body text look identical (whitespace-level change)")
    return "Section " + old_node.numbering + ": " + "; ".join(parts)


def _sort_key(numbering: str):
    """Sort numberings naturally (2.1 before 2.10, not string-lexically)."""
    return [int(p) for p in numbering.split(".")]
