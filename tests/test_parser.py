"""
Unit tests for app/parser/pdf_parser.py.

These specifically target the real irregularities found by manually
inspecting data/ct200_manual.pdf (documented in APPROACH.md):

1. Duplicate heading text ("Error Codes" appears at both 4.2 and 7.1)
   -> must produce two distinct node IDs with distinct parents.
2. A heading with a missing intermediate parent level (2.1.1.1 appears
   with no 2.1.1 heading in the document at all)
   -> parser must not crash, and must attach it to the nearest existing
      ancestor (2.1) rather than silently dropping it or mis-attaching
      it to the wrong parent.
3. A numbered list that looks like headings but is body text
   (the "1. Normal / 2. Elevated / ..." classification list inside
   section 3.3) -> must NOT be split into separate heading nodes; it
   must stay attached as body text under 3.3.
4. (Bonus / defensive) Out-of-order siblings: 3.4 appears in the
   document before 3.3 -> both must still become children of "3",
   in document order, not reordered by their numeric label.

Run with: pytest tests/test_parser.py -v
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.parser.pdf_parser import build_tree, flatten

PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ct200_manual.pdf")


def _get_nodes():
    tree = build_tree(PDF_PATH, document_id="test")
    return flatten(tree)


def test_duplicate_heading_names_produce_distinct_nodes():
    """Two 'Error Codes' headings (4.2 and 7.1) must be distinct nodes."""
    nodes = _get_nodes()
    error_code_nodes = [n for n in nodes if n.heading == "Error Codes"]

    assert len(error_code_nodes) == 2, (
        f"expected 2 nodes named 'Error Codes', found {len(error_code_nodes)}"
    )

    ids = {n.id for n in error_code_nodes}
    assert len(ids) == 2, "duplicate-named nodes must have distinct IDs"

    numberings = {n.numbering for n in error_code_nodes}
    assert numberings == {"4.2", "7.1"}, f"unexpected numberings: {numberings}"

    # They must also have different parents (4.2 under "4", 7.1 under "7")
    parent_ids = {n.parent_id for n in error_code_nodes}
    assert len(parent_ids) == 2, "the two 'Error Codes' nodes must have different parents"


def test_missing_intermediate_level_does_not_crash_and_attaches_sensibly():
    """
    2.1.1.1 Battery Life appears with no 2.1.1 in the document.
    The parser must not crash, and must attach 2.1.1.1 to the nearest
    existing ancestor by level (2.1 General Specifications), not to the
    document root and not dropped entirely.
    """
    nodes = _get_nodes()
    battery_node = next((n for n in nodes if n.numbering == "2.1.1.1"), None)

    assert battery_node is not None, "2.1.1.1 node was dropped entirely"
    assert battery_node.level == 4, "level should be derived from the 4 dot-separated numbers"

    parent = next(n for n in nodes if n.id == battery_node.parent_id)
    assert parent.numbering == "2.1", (
        f"expected 2.1.1.1 to attach to 2.1 (nearest real ancestor), "
        f"but it attached to {parent.numbering!r}"
    )


def test_numbered_list_inside_body_is_not_treated_as_headings():
    """
    Section 3.3 contains a numbered classification list
    ("1. Normal...", "2. Elevated...", etc.) that visually resembles
    the heading numbering pattern. These must remain body text under
    3.3, not become sibling/child heading nodes.
    """
    nodes = _get_nodes()

    result_display = next(
        (n for n in nodes if n.numbering == "3.3"), None
    )
    assert result_display is not None, "3.3 Result Display node not found"

    # The classification list text must be present in 3.3's body...
    assert "Normal" in result_display.body_text
    assert "Hypertensive Crisis" in result_display.body_text

    # ...and must NOT exist as separate heading nodes anywhere in the tree.
    fake_heading_numberings = {"1", "2", "3", "4", "5"}
    top_level_numberings = {n.numbering for n in nodes if n.level == 1}
    # The real top-level sections are 1-8 (Device Overview, etc.), so we
    # instead check no node's heading text IS one of the list line texts.
    list_like_headings = [
        n for n in nodes
        if n.heading.strip().startswith(("Normal:", "Elevated:", "Hypertension Stage"))
    ]
    assert list_like_headings == [], (
        f"numbered list items were incorrectly parsed as heading nodes: "
        f"{[n.heading for n in list_like_headings]}"
    )


def test_out_of_order_siblings_both_attach_to_correct_parent():
    """
    3.4 Auto Shutoff appears in the document BEFORE 3.3 Result Display
    (physically out of numeric order). Both must still become children
    of section 3 (Device Operation), preserving document order rather
    than being sorted/rejected.
    """
    nodes = _get_nodes()
    device_operation = next(n for n in nodes if n.numbering == "3")

    child_numberings = {
        n.numbering for n in nodes if n.parent_id == device_operation.id
    }
    assert "3.3" in child_numberings
    assert "3.4" in child_numberings

    # Confirm document order is preserved: 3.4's order_index should be
    # LESS than 3.3's, since it physically appears first in the PDF.
    node_34 = next(n for n in nodes if n.numbering == "3.4")
    node_33 = next(n for n in nodes if n.numbering == "3.3")
    assert node_34.order_index < node_33.order_index, (
        "expected 3.4 to appear before 3.3 in document order"
    )


def test_every_node_has_a_content_hash():
    """Sanity check: every real node (excluding root) has a non-empty hash."""
    nodes = _get_nodes()
    for n in nodes:
        if n.heading == "ROOT":
            continue
        assert n.content_hash, f"node {n.numbering} ({n.heading!r}) has no content_hash"
        assert len(n.content_hash) == 64, "expected a SHA-256 hex digest (64 chars)"
