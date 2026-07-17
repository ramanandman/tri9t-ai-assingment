# CardioTrack CT-200 Manual QA System

A backend that parses the CT-200 device manual (PDF) into a versioned,
browsable tree, lets a user select sections, generates QA test case
ideas from them via an LLM, and detects when previously generated test
cases go stale as the manual is updated.

## Tech stack

- FastAPI + Pydantic (API layer, validation)
- SQLAlchemy + SQLite (document tree, versions, selections)
- pdfplumber (PDF text/table/font extraction)
- Groq (LLM provider, `llama-3.3-70b-versatile`)
- A flat JSON file (`generated_test_cases.json`) as the NoSQL-style
  store for LLM-generated output (see APPROACH.md for why this instead
  of MongoDB)

## Setup

1. Clone the repo and `cd` into it.
2. Create and activate a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate        # Windows
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Create a `.env` file in the project root (never commit this file):
   ```
   GROQ_API_KEY=your_groq_api_key_here
   ```
   Get a free key at https://console.groq.com (API Keys section).

## Running the server

```
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/docs for the interactive API docs
(Swagger UI). All endpoints below can be exercised directly from that
page.

## Running the tests

```
pytest tests/test_parser.py -v
```

5 unit tests cover the parser irregularities found in the manual
(duplicate headings, a missing intermediate heading level, a numbered
list that is not a heading, out-of-order sibling sections, and content
hashing). See APPROACH.md for what each one targets and why.

## How to trigger the v1 -> v2 re-ingestion flow

This is the core flow the assignment asks to see demonstrated. All
steps below can be run through http://127.0.0.1:8000/docs.

1. **Create a document:**
   `POST /documents?name=CardioTrack CT-200 Manual`
   → returns a `document_id`.

2. **Ingest v1:**
   `POST /documents/{document_id}/versions`
   ```json
   { "pdf_path": "data/ct200_manual.pdf", "source_filename": "ct200_manual.pdf" }
   ```

3. **Browse v1:**
   `GET /documents/{document_id}/sections` (defaults to latest = v1 at this point)

4. **Create a selection** (pin specific v1 nodes):
   `POST /selections`
   ```json
   {
     "name": "Pressure Safety Tests",
     "nodes": [ { "node_id": "...", "version_id": "..." } ]
   }
   ```

5. **Generate QA test cases from the selection:**
   `POST /selections/{selection_id}/generate`
   → stores the generated test cases linked to the exact node IDs and
   content hashes used.

6. **Ingest v2** (re-ingest the modified manual as a NEW version, without
   touching v1's data):
   `POST /documents/{document_id}/versions`
   ```json
   { "pdf_path": "data/ct200_manual_v2.pdf", "source_filename": "ct200_manual_v2.pdf" }
   ```

7. **Check whether a specific node changed between versions:**
   `GET /nodes/{node_id}/changes`
   → returns `status: unchanged | changed | added | removed` plus a
   lightweight diff summary.

8. **Retrieve the test cases generated in step 5, and see the staleness flag:**
   `GET /test-cases/{generation_id}`
   → if any of that generation's source sections changed in v2, the
   response includes `"staleness": { "is_stale": true, "stale_nodes": [...] }`.

## API summary

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness check |
| POST | `/documents` | create a document |
| GET | `/documents` | list documents |
| POST | `/documents/{id}/versions` | ingest a new version from a PDF path |
| GET | `/documents/{id}/versions` | list versions |
| GET | `/documents/{id}/sections` | list top-level sections (version-aware) |
| GET | `/nodes/{id}` | node detail with children |
| GET | `/search?q=...` | search headings/body text |
| GET | `/nodes/{id}/changes` | staleness/diff vs. prior version |
| POST | `/selections` | create a version-pinned selection |
| GET | `/selections/{id}` | fetch a selection |
| POST | `/selections/{id}/generate` | generate QA test cases (LLM) |
| GET | `/test-cases/{id}` | fetch a generation, with staleness flag |
| GET | `/selections/{id}/test-cases` | all generations for a selection |
| GET | `/nodes/{id}/test-cases` | all generations that used a given node |

See APPROACH.md for the data model, parsing decisions, version-matching
strategy, LLM prompt/retry design, and the required decision log.
