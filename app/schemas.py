"""
Pydantic schemas used by the API routes for request/response validation.
"""

from pydantic import BaseModel


class NodeSummary(BaseModel):
    """A node without its children or full body - used in list views."""
    id: str
    numbering: str
    heading: str
    level: int
    content_hash: str
    order_index: int

    class Config:
        from_attributes = True


class NodeDetail(BaseModel):
    """A single node including full body text and children."""
    id: str
    version_id: str
    parent_id: str | None
    numbering: str
    heading: str
    level: int
    body_text: str
    content_hash: str
    order_index: int
    children: list["NodeDetail"] = []

    class Config:
        from_attributes = True


class NodeChangeResponse(BaseModel):
    numbering: str
    status: str
    old_node_id: str | None
    new_node_id: str | None
    summary: str


class DocumentVersionSummary(BaseModel):
    id: str
    version_number: int
    source_filename: str

    class Config:
        from_attributes = True


class IngestVersionRequest(BaseModel):
    pdf_path: str
    source_filename: str | None = None


class SelectionNodeInput(BaseModel):
    """A single node+version pin submitted when creating a selection."""
    node_id: str
    version_id: str


class CreateSelectionRequest(BaseModel):
    name: str
    nodes: list[SelectionNodeInput]


class SelectionNodeResponse(BaseModel):
    node_id: str
    version_id: str
    heading: str
    numbering: str

    class Config:
        from_attributes = True


class SelectionResponse(BaseModel):
    id: str
    name: str
    nodes: list[SelectionNodeResponse]

    class Config:
        from_attributes = True


class TestCaseResponse(BaseModel):
    title: str
    preconditions: list[str]
    steps: list[str]
    expected_result: str


class GenerationResponse(BaseModel):
    id: str
    selection_id: str
    generated_at: str
    test_cases: list[TestCaseResponse]
