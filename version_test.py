"""
Throwaway script to test ingest_version + diff_versions end to end.
Run with: python version_test.py
"""

from app.database import init_db, SessionLocal
from app.models import Document
from app.services.versioning import ingest_version, diff_versions

init_db()
db = SessionLocal()

doc = Document(name="CardioTrack CT-200 Manual (version test)")
db.add(doc)
db.flush()
db.commit()

v1 = ingest_version(db, doc.id, "data/ct200_manual.pdf", "ct200_manual.pdf")
print(f"Ingested version {v1.version_number} ({v1.id})")

v2 = ingest_version(db, doc.id, "data/ct200_manual_v2.pdf", "ct200_manual_v2.pdf")
print(f"Ingested version {v2.version_number} ({v2.id})")

changes = diff_versions(db, v1.id, v2.id)

changed = [c for c in changes if c.status == "changed"]
added = [c for c in changes if c.status == "added"]
removed = [c for c in changes if c.status == "removed"]
unchanged = [c for c in changes if c.status == "unchanged"]

print(f"\n{len(changed)} changed, {len(added)} added, {len(removed)} removed, {len(unchanged)} unchanged\n")

for c in changed + added + removed:
    print(f"[{c.status.upper()}] {c.numbering}: {c.summary}")

db.close()
