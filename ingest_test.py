"""
Throwaway script to sanity-check that parsing -> saving to DB -> querying
back actually works end to end. Not part of the final API - just a
manual check. Safe to delete once you trust the pipeline (or keep it,
it's harmless).

Run with: python ingest_test.py
"""

from app.database import init_db, SessionLocal
from app.models import Document, DocumentVersion, Node
from app.parser.pdf_parser import build_tree, flatten

init_db()
db = SessionLocal()

# Create the Document and its first version
doc = Document(name="CardioTrack CT-200 Manual")
db.add(doc)
db.flush()

version1 = DocumentVersion(document_id=doc.id, version_number=1, source_filename="ct200_manual.pdf")
db.add(version1)
db.flush()

# Parse and persist nodes
tree = build_tree("data/ct200_manual.pdf", document_id="doc")
parsed_nodes = flatten(tree)

# map from parser's node.id -> db Node.id, so we can resolve parent_id
id_map = {}
db_nodes = []
for pn in parsed_nodes:
    db_node = Node(
        version_id=version1.id,
        numbering=pn.numbering,
        heading=pn.heading,
        level=pn.level,
        body_text=pn.body_text,
        content_hash=pn.content_hash,
        order_index=pn.order_index,
        parent_id=None,  # filled in second pass below
    )
    db.add(db_node)
    db.flush()
    id_map[pn.id] = db_node.id
    db_nodes.append((pn, db_node))

for pn, db_node in db_nodes:
    if pn.parent_id:
        db_node.parent_id = id_map[pn.parent_id]

db.commit()

# Now query it back
count = db.query(Node).filter(Node.version_id == version1.id).count()
print(f"Saved {count} nodes for version 1")

sample = db.query(Node).filter(Node.numbering == "4.2").first()
print(f"Sample query - numbering=4.2: heading={sample.heading!r}, hash={sample.content_hash[:12]}...")

db.close()
print("\nDone. Check for a new file 'ct200.db' in this folder.")
