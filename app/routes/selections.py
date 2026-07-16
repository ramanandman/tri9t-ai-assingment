"""
Selection routes:
  POST /selections      - create a new named, version-pinned selection
  GET  /selections/{id} - fetch a selection and its pinned nodes

Design decision (documented here + APPROACH.md): submitting the same
selection (same name/nodes) twice creates a SEPARATE new Selection row
each time, rather than detecting and merging duplicates. We chose this
because a user may deliberately want two distinct selections with
identical scope made at different times (e.g. re-running the same test
scope in a later sprint), and silently deduping would lose that intent.
The cost is that "duplicate" selections can pile up - acceptable given
the assignment's scope and time constraints (see APPROACH.md "what I'd
do differently").
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Selection, SelectionNode, Node
from app.schemas import CreateSelectionRequest, SelectionResponse, SelectionNodeResponse

router = APIRouter()


@router.post("/selections", response_model=SelectionResponse)
def create_selection(request: CreateSelectionRequest, db: Session = Depends(get_db)):
    if not request.nodes:
        raise HTTPException(status_code=400, detail="A selection must include at least one node.")

    selection = Selection(name=request.name)
    db.add(selection)
    db.flush()

    response_nodes = []
    for item in request.nodes:
        node = db.query(Node).filter(Node.id == item.node_id, Node.version_id == item.version_id).first()
        if not node:
            raise HTTPException(
                status_code=404,
                detail=f"Node {item.node_id} not found in version {item.version_id} - "
                       f"check that the node_id/version_id pair actually corresponds to an existing node.",
            )
        sel_node = SelectionNode(selection_id=selection.id, node_id=node.id, version_id=node.version_id)
        db.add(sel_node)
        response_nodes.append(
            SelectionNodeResponse(node_id=node.id, version_id=node.version_id, heading=node.heading, numbering=node.numbering)
        )

    db.commit()

    return SelectionResponse(id=selection.id, name=selection.name, nodes=response_nodes)


@router.get("/selections/{selection_id}", response_model=SelectionResponse)
def get_selection(selection_id: str, db: Session = Depends(get_db)):
    selection = db.query(Selection).filter(Selection.id == selection_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail=f"Selection {selection_id} not found")

    response_nodes = []
    for item in selection.items:
        response_nodes.append(
            SelectionNodeResponse(
                node_id=item.node.id,
                version_id=item.version_id,
                heading=item.node.heading,
                numbering=item.node.numbering,
            )
        )

    return SelectionResponse(id=selection.id, name=selection.name, nodes=response_nodes)
