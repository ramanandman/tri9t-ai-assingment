"""
Main FastAPI application entrypoint.

Run with: uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs for interactive API docs.
"""

from fastapi import FastAPI

from app.database import init_db
from app.routes import documents, nodes, selections, test_cases

app = FastAPI(title="CT-200 Manual QA System", version="1.0.0")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(documents.router)
app.include_router(nodes.router)
app.include_router(selections.router)
app.include_router(test_cases.router)
