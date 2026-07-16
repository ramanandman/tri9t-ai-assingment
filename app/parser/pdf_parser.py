"""
PDF Parser for the CT-200 manual.

Design summary (see APPROACH.md for full reasoning):
- A line is treated as a HEADING only if it is BOLD *and* starts with a
  numbering pattern like "1", "1.1", "2.1.1.1" followed by a title.
  Bold-only is not enough (table header rows are bold too).
  Numbering-only is not enough (numbered list items like "1. Normal:..."
  match the same regex but are plain/regular weight, not bold).
- Heading "level" is derived from how many dot-separated numbers appear
  (e.g. "2.1.1.1" -> level 4), NOT from font size alone, because we found
  a real level-4 heading ("2.1.1.1 Battery Life...") rendered at the same
  font size as body text (11.0pt) - only bold distinguishes it.
- Parent/child relationships are built with a stack. We do NOT assume
  levels increase by exactly 1 or that siblings appear in numeric order;
  the manual has a heading with a missing intermediate parent (2.1.1.1
  with no 2.1.1) and two headings that appear physically out of numeric
  order (3.4 appears before 3.3). We handle both explicitly:
    - missing intermediate levels: we pop/push based on the heading's
      OWN level number, not on "current level + 1", so a jump from
      level 2 straight to level 4 is legal - it just attaches to the
      nearest currently-open ancestor (level 2 in that case).
    - out-of-order siblings: we build the tree from LITERAL DOCUMENT
      ORDER (top-to-bottom reading order), not from sorting by the
      numeric heading label. 3.4 becomes a child of "3." immediately
      when it's encountered, regardless of what number it carries.
- Duplicate heading text (two "Error Codes" headings, at 4.2 and 7.1)
  is handled because node identity is based on a unique auto-incrementing
  ID + the heading's numeric path + its position in document order, never
  on heading text alone. Two nodes can have identical `heading` values
  and still be distinct nodes with distinct parents/IDs.
- Tables are extracted separately via pdfplumber's table-detection
  (not the character-stream text), because the plain text stream merges
  table cells together with no separator (e.g. "ParameterValue").
"""

import re
import hashlib
from dataclasses import dataclass, field

import pdfplumber

# A heading number looks like "1", "1.1", "2.1.1.1" etc, followed by a
# space and then a title. Anchored to the start of the line.
HEADING_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")

# Soft hyphen / ligature cleanup seen in this PDF's font rendering.
SOFT_HYPHEN_CHARS = ["\u2011", "\u00ad", "‑"]


def clean_text(text: str) -> str:
    """Normalize odd characters produced by this PDF's font encoding."""
    for ch in SOFT_HYPHEN_CHARS:
        text = text.replace(ch, "-")
    text = text.replace("\ufb00", "ff")  # 'ﬀ' ligature -> 'ff'
    text = text.replace("\ufb01", "fi")  # 'ﬁ' ligature -> 'fi'
    text = text.replace("\ufb02", "fl")  # 'ﬂ' ligature -> 'fl'
    return text


@dataclass
class RawLine:
    text: str
    size: float
    bold: bool
    page: int
    top: float


@dataclass
class ParsedNode:
    id: str
    heading: str
    level: int
    numbering: str  # e.g. "2.1.1.1", or "" for the document root
    body_text: str
    parent_id: str | None
    content_hash: str
    order_index: int  # position in document reading order
    children: list = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id,
            "heading": self.heading,
            "level": self.level,
            "numbering": self.numbering,
            "body_text": self.body_text,
            "parent_id": self.parent_id,
            "content_hash": self.content_hash,
            "order_index": self.order_index,
        }


def extract_lines(pdf_path: str) -> list[RawLine]:
    """Pull every line of text from the PDF with font size + bold flag."""
    lines: list[RawLine] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            grouped: dict[float, list] = {}
            for c in page.chars:
                key = round(c["top"], 1)
                grouped.setdefault(key, []).append(c)
            for top in sorted(grouped.keys()):
                chars = grouped[top]
                text = "".join(c["text"] for c in chars).strip()
                if not text:
                    continue
                # A line counts as bold if the majority of its
                # (non-trivial) characters come from a "Bold" font name.
                bold_chars = sum(1 for c in chars if "Bold" in c.get("fontname", ""))
                is_bold = bold_chars >= max(1, len(chars) // 2)
                size = max(
                    (round(c["size"], 1) for c in chars if c.get("fontname") and "Bold" in c["fontname"]),
                    default=round(chars[0]["size"], 1),
                )
                lines.append(RawLine(text=clean_text(text), size=size, bold=is_bold, page=page_num + 1, top=top))
    return lines


def is_heading_line(line: RawLine) -> tuple[str, str] | None:
    """
    Return (numbering, title) if this line is a real heading, else None.
    Requires BOTH: bold font AND a leading numbering pattern.
    """
    if not line.bold:
        return None
    match = HEADING_PATTERN.match(line.text)
    if not match:
        return None
    numbering, title = match.group(1), match.group(2)
    # Reject obviously-too-long "titles" - real headings in this manual
    # are short; a heading-shaped bold line with a full sentence after it
    # is treated conservatively as NOT a heading (defensive guard against
    # unseen irregularities, e.g. a bold warning sentence starting with a
    # number).
    if len(title) > 80:
        return None
    return numbering, title


def build_tree(pdf_path: str, document_id: str = "doc") -> ParsedNode:
    """
    Parse the PDF into a tree of ParsedNode, rooted at a synthetic
    top-level node representing the whole document.
    """
    raw_lines = extract_lines(pdf_path)

    root = ParsedNode(
        id=f"{document_id}-root",
        heading="ROOT",
        level=0,
        numbering="",
        body_text="",
        parent_id=None,
        content_hash="",
        order_index=-1,
    )

    # stack of currently-open nodes, index 0 = root
    stack: list[ParsedNode] = [root]
    order_counter = 0
    body_buffer: list[str] = []
    current_node = root

    def flush_body():
        if body_buffer:
            current_node.body_text = (current_node.body_text + "\n" + "\n".join(body_buffer)).strip()
            body_buffer.clear()

    for line in raw_lines:
        # Skip the big document title block (size 22, appears before any
        # numbered heading) - it's front matter, not a tree node.
        heading_info = is_heading_line(line)

        if heading_info is None:
            body_buffer.append(line.text)
            continue

        # We hit a real heading -> flush whatever body text belongs to
        # the current node, then create the new node.
        flush_body()

        numbering, title = heading_info
        level = len(numbering.split("."))

        # Pop the stack until we find a node whose level is LESS than
        # this heading's level - that becomes the parent. This correctly
        # handles both a missing intermediate level (jump from level 2
        # straight to level 4 just attaches to the level-2 node) and
        # out-of-order siblings (a "3.4" after "3.2" still just pops
        # back to "3." and attaches there, regardless of the number).
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1]

        order_counter += 1
        node_id = f"{document_id}-n{order_counter}"
        node = ParsedNode(
            id=node_id,
            heading=title,
            level=level,
            numbering=numbering,
            body_text="",
            parent_id=parent.id,
            content_hash="",
            order_index=order_counter,
        )
        parent.children.append(node)
        stack.append(node)
        current_node = node

    flush_body()

    # Compute content hashes bottom-up isn't necessary here since hash is
    # per-node (heading + own body text), not a rollup of children.
    def hash_node(n: ParsedNode):
        payload = (n.heading + "|" + n.body_text).encode("utf-8")
        n.content_hash = hashlib.sha256(payload).hexdigest()
        for child in n.children:
            hash_node(child)

    hash_node(root)
    return root


def flatten(node: ParsedNode) -> list[ParsedNode]:
    """Flatten the tree into a list (depth-first), useful for DB inserts/tests."""
    result = [node]
    for child in node.children:
        result.extend(flatten(child))
    return result


def _looks_like_real_table(rows: list) -> bool:
    """
    pdfplumber's table-detector also fires on ordinary justified body
    text (word-spacing gaps get misread as column boundaries), producing
    spurious 1-2 row "tables" full of None/empty cells. We found this
    empirically on this exact PDF (e.g. a fake table containing just
    ['Pressure', 'range']). A real table in this document has at least
    3 rows and every cell in every row is non-empty, non-None text.
    This is a deliberate, documented heuristic - not a generic solution -
    since the assignment scope is this specific manual.
    """
    if len(rows) < 3:
        return False
    for row in rows:
        for cell in row:
            if cell is None or str(cell).strip() == "":
                return False
    return True


def extract_tables(pdf_path: str) -> list[dict]:
    """
    Extract tables using pdfplumber's table-detection (not the plain
    character stream, which merges cell text together with no
    separator). Returns raw table data tagged with the page number;
    associating a table to its owning section node is done by the
    caller based on page/position, since pdfplumber tables don't carry
    heading context themselves.

    Filters out false-positive "tables" that pdfplumber detects from
    ordinary paragraph text spacing - see _looks_like_real_table.
    """
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            for table in page.extract_tables():
                if _looks_like_real_table(table):
                    tables.append({"page": page_num + 1, "rows": table})
    return tables


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else "data/ct200_manual.pdf"
    tree = build_tree(path)
    nodes = flatten(tree)
    print(f"Parsed {len(nodes) - 1} nodes (excluding root) from {path}\n")
    for n in nodes:
        if n.id == tree.id:
            continue
        indent = "  " * (n.level - 1)
        preview = n.body_text[:60].replace("\n", " ")
        print(f"{indent}[{n.numbering}] {n.heading}  (level={n.level}, parent={n.parent_id})")
        if preview:
            print(f"{indent}    body: {preview}...")
