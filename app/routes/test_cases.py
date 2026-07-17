"""
Test case generation & retrieval routes:
  POST /selections/{selection_id}/generate  - generate QA test cases for a selection
  GET  /test-cases/{generation_id}          - fetch a specific generation, with staleness flag
  GET  /selections/{selection_id}/test-cases - fetch all generations for a selection
  GET  /nodes/{node_id}/test-cases          - fetch all generations that used this node
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Selection, Node
from app.schemas import GenerationResponse, TestCaseResponse
from app.services.llm_generator import (
    generate_test_cases,
    save_generation,
    get_generation,
    get_generations_for_selection,
    get_generations_for_node,
    LLMGenerationError,
)

router = APIRouter()


def _generation_to_response(record: dict, staleness: dict | None = None) -> dict:
    resp = {
        "id": record["id"],
        "selection_id": record["selection_id"],
        "generated_at": record["generated_at"],
        "test_cases": record["test_cases"],
    }
    if staleness is not None:
        resp["staleness"] = staleness
    return resp


def _check_staleness(db: Session, record: dict) -> dict:
    """
    Compare each source node's stored content_hash against that node's
    CURRENT content_hash in the database. If ANY source node's hash no
    longer matches, the whole generation is considered stale (we flag
    at the generation level, and also list which specific nodes drifted -
    a generation drawing from multiple sections is only as fresh as its
    least-fresh source).
    """
    stale_nodes = []
    for source in record["source_nodes"]:
        current_node = db.query(Node).filter(Node.id == source["node_id"]).first()
        if current_node is None:
            stale_nodes.append({"node_id": source["node_id"], "numbering": source["numbering"], "reason": "node no longer exists"})
        elif current_node.content_hash != source["content_hash"]:
            stale_nodes.append({"node_id": source["node_id"], "numbering": source["numbering"], "reason": "content changed"})

    return {
        "is_stale": len(stale_nodes) > 0,
        "stale_nodes": stale_nodes,
    }


@router.post("/selections/{selection_id}/generate", response_model=GenerationResponse)
def generate_for_selection(selection_id: str, db: Session = Depends(get_db)):
    selection = db.query(Selection).filter(Selection.id == selection_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail=f"Selection {selection_id} not found")
    if not selection.items:
        raise HTTPException(status_code=400, detail="Selection has no nodes to generate from")

    sections = []
    source_nodes = []
    for item in selection.items:
        node = item.node
        sections.append({"numbering": node.numbering, "heading": node.heading, "body_text": node.body_text})
        source_nodes.append({
            "node_id": node.id,
            "version_id": node.version_id,
            "numbering": node.numbering,
            "content_hash": node.content_hash,
        })

    try:
        result = generate_test_cases(sections)
    except LLMGenerationError as e:
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {e}")

    record = save_generation(selection_id, source_nodes, result)
    return _generation_to_response(record)


@router.get("/test-cases/{generation_id}")
def get_test_case_generation(generation_id: str, db: Session = Depends(get_db)):
    record = get_generation(generation_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Generation {generation_id} not found")
    staleness = _check_staleness(db, record)
    return _generation_to_response(record, staleness)


@router.get("/selections/{selection_id}/test-cases")
def get_test_cases_for_selection(selection_id: str, db: Session = Depends(get_db)):
    records = get_generations_for_selection(selection_id)
    return [_generation_to_response(r, _check_staleness(db, r)) for r in records]


@router.get("/nodes/{node_id}/test-cases")
def get_test_cases_for_node(node_id: str, db: Session = Depends(get_db)):
    records = get_generations_for_node(node_id)
    return [_generation_to_response(r, _check_staleness(db, r)) for r in records]
