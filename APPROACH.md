# Approach Document

## 1. Data model

- **Document**: one row per logical document. Created once, never
  mutated.
- **DocumentVersion**: one row per ingestion (v1, v2, ...). Belongs to
  a Document. Ingesting a new version never touches an existing
  version's rows.
- **Node**: one row per tree node (heading + body text). Belongs to
  exactly one DocumentVersion. Fields: `numbering` (e.g. `"2.1.1.1"`),
  `heading`, `level`, `body_text`, `content_hash` (SHA-256 of
  heading+body), `order_index` (document reading order), `parent_id`.
- **Selection / SelectionNode**: a named set of (node_id, version_id)
  pins. `version_id` is stored explicitly on SelectionNode even though
  it's derivable from the Node row, because the assignment specifically
  asks selections to record the version they were made against, and
  I wanted that requirement visible directly in the schema.
- **Generated test cases**: stored outside SQL, in a flat JSON file
  (see section 5), each record linked to `selection_id` and to the
  exact `node_id` + `content_hash` of every source section used.

A deliberate simplicity choice: a "logical section" (e.g. "section
3.3") is NOT a single stable row that gets edited across versions.
Instead, v1's "3.3" and v2's "3.3" are two separate Node rows, matched
at query time by `numbering`. This makes "did this change" a simple
two-row hash comparison instead of requiring a mutable-history/audit
log design. The tradeoff (no single ID that spans versions, and a
matching strategy that can be fooled by renumbering) is discussed in
section 3.

## 2. Parsing decisions

The manual was inspected manually (opened the PDF and read it, then
extracted font metadata with pdfplumber) before writing any parsing
code. Irregularities found, and how each is handled:

1. **Missing intermediate heading level.** `2.1.1.1 Battery Life Under
   Typical Use` appears with no `2.1.1` anywhere in the document. The
   parser attaches a heading to the nearest currently-open ancestor by
   *level*, not by assuming `level - 1` exists - a stack is popped
   until an ancestor with a strictly lower level is found, so a jump
   from level 2 straight to level 4 just attaches to the level-2 node.
   Tested in `test_missing_intermediate_level_does_not_crash_and_attaches_sensibly`.

2. **Out-of-order siblings.** `3.4 Auto Shutoff` physically appears in
   the PDF *before* `3.3 Result Display and Classification`. The tree
   is built strictly in document reading order (top to bottom), not by
   sorting on the numeric label, so both attach correctly to their
   parent in the order they were read. Tested in
   `test_out_of_order_siblings_both_attach_to_correct_parent`.

3. **Duplicate heading text.** "Error Codes" appears as both `4.2` and
   `7.1`. Node identity is never based on heading text - each node gets
   a fresh, order-based ID regardless of what other nodes are named,
   so two "Error Codes" nodes are simply two distinct nodes with
   distinct parents. Tested in
   `test_duplicate_heading_names_produce_distinct_nodes`.

4. **A numbered list that looks like headings.** Section 3.3 contains
   a classification list ("1. Normal: systolic < 120...", "2.
   Elevated...") that matches the same `\d+\.\s+text` pattern as a real
   heading. Font inspection showed real headings are **bold**
   (Nimbus Sans Bold) while this list is regular weight. The heading
   detector requires BOTH a numbering-pattern match AND bold font -
   neither signal alone is reliable (bold alone would also catch bold
   table headers; the numbering pattern alone can't distinguish a
   heading from a numbered list item). Tested in
   `test_numbered_list_inside_body_is_not_treated_as_headings`.

5. **Tables with wrapped cell text.** The specs table (2.1) and error
   code table (4.2) have multi-line cell content (e.g. "Motion artifact
   detected during measurement" wraps across two lines within one
   cell). The plain character-stream text merges these with no
   separator (e.g. `"ParameterValue"`), so tables are extracted
   separately via `pdfplumber.extract_tables()`, which correctly keeps
   wrapped lines within a cell.

6. **False-positive table detection.** `pdfplumber`'s table detector
   also fires on ordinary justified body-text word-spacing, producing
   spurious 1-2 row "tables" (e.g. a fake table containing just
   `["Pressure", "range"]`). I found this by printing every detected
   table and noticing several 1-2 row tables full of sparse/empty
   cells that didn't correspond to anything in the actual document. A
   real table in this document has at least 3 rows with every cell
   populated; this heuristic is not generic, and is documented as
   specific to this document's real tables.

7. **Extraction artifacts (found while diffing v1 vs v2).** A
   standalone soft-hyphen character sometimes gets extracted as its
   own "line" depending on how a line wraps across a page break. Left
   unfiltered, this produced a false "changed" flag on section 8.1
   between v1 and v2 even though the visible text was semantically
   identical. Lines that are only hyphen/soft-hyphen characters are
   now filtered out during extraction. I found this by literally
   diffing the raw body text of the two versions side by side after
   noticing an unexplained change.

## 3. Version-matching strategy and its known failure mode

**Strategy:** nodes are matched across versions by `numbering` (e.g.
`"3.3"`), not by heading text (ruled out by the duplicate-heading
case) and not by a persistent database ID (nodes are recreated fresh
per version, by design - see section 1).

**Known failure mode:** if a section is renumbered between versions -
e.g. an editor inserts a new section 3.3 and pushes the old 3.3 down to
become 3.4 - the matcher will report the old section as REMOVED and
the shifted section as ADDED, even though a human would recognize it
as the same content that just moved. Detecting that would require
fuzzy title/body similarity matching across different numbering
labels, which I deliberately did not build; the assignment's real test
document (v1 -> v2) does not renumber any section, so I prioritized
correctness on the concrete cases actually present over a
renumbering-robust generic matcher. This is exactly the kind of
tradeoff item 2 of the decision log below is about.

**A second, subtler limitation found empirically:** content-hash-based
change detection is sensitive to *any* text difference, including pure
extraction artifacts. Diffing the real v1/v2 PDFs, I found section 8.1
flagged as "changed" purely because v1's PDF extraction dropped a
hyphen ("CT200" vs "CT-200", "noninvasive" vs "non-invasive") that v2
did not - the actual regulatory meaning is identical. So: **a
one-word/wording-only change and a meaningful change (e.g. a changed
pressure threshold) are currently treated identically** by the
staleness system - both just flip `content_hash` and set
`is_stale: true`. I chose not to build semantic diffing (e.g. ignoring
punctuation-only changes, or flagging numeric changes specially) given
the assignment's time scope, but it is the single most honest
limitation of the whole system: it can raise false "stale" flags on
cosmetic changes, and can't distinguish a critical threshold change
from a typo fix in its `status` field alone - a human still needs to
read the diff summary/body text to judge severity.

## 4. Selection and duplicate-submission policy

Submitting the same selection (same name, same nodes) twice, or
generating test cases for the same selection twice, both create a
**new** row each time rather than being deduplicated. Reasoning: a user
might deliberately want two distinct selections or generations made at
different times with the same scope (e.g. re-running QA ideation for
the same section next sprint), and silently merging/blocking that
would lose real intent without being asked to. The cost is that
"duplicate-looking" selections/generations can accumulate over time;
I judged this an acceptable tradeoff given the assignment's scope,
and it's a one-line policy change if a real product needed dedup.

## 5. LLM prompt design and structured-output / retry strategy

**Provider:** Groq (`llama-3.3-70b-versatile`), chosen for its free
tier and speed; the design is provider-agnostic (any chat-completions
API would slot in the same way).

**Prompt:** the system prompt tells the model exactly which JSON shape
to return (`{"test_cases": [{"title", "preconditions", "steps",
"expected_result"}]}`) and explicitly forbids markdown fences or
commentary. The user prompt reconstructs the selection's pinned
sections (heading + body, in numbering order) as the source text, and
asks for 3-5 test cases grounded only in that text.

**Validation:** the raw response is never trusted as-is. It's first
stripped of common formatting mistakes (a leading/trailing ` ```json `
fence, if the model adds one anyway), then parsed as JSON, then
validated against a strict Pydantic schema (`GeneratedTestCases`). Any
failure at either step (invalid JSON, or valid JSON with the wrong
shape/types) is treated as a hard failure, not "close enough."

**Retry policy:** on failure, the system retries **exactly once**,
sending the model its own broken response plus the specific
parse/validation error and asking it to correct it. If the second
attempt also fails, the endpoint returns a controlled `502` error with
the underlying cause - it does not save partial/malformed data, and it
does not retry indefinitely. This was a deliberate choice: "it usually
works" is explicitly called out in the assignment brief as not a real
design, so the system needs a defined, finite failure path.

**Storage:** generated output is stored in a flat JSON file
(`generated_test_cases.json`) rather than a real MongoDB instance -
this is the assignment's "well-justified JSON store" option.
Justification: this data has no need for relational joins or complex
querying - it's always fetched by `selection_id` or `node_id`, both of
which a flat file indexed by generation ID handles fine at this scale.
Standing up a MongoDB instance (local or Atlas) would add an external
dependency without a concrete benefit for a take-home assignment of
this size. Each stored record links to the exact `node_id` +
`content_hash` of every source section, which is what makes retrieval-
time staleness checking possible (see `/test-cases/{id}` in the API,
which recomputes each source node's *current* hash and compares it
against what's stored).

## 6. Decision log

**1. What's the one part of this system most likely to silently give
wrong results without erroring? How would you catch it?**

The PDF parser's heading detection. It's built on empirically-observed
signals (bold font + numbering pattern) from *this specific* document.
A subtly different manual - or even a different export/print of the
same manual with different font embedding - could produce a tree that
*looks* structurally fine (no crash, no missing sections) but has
content silently misattached to the wrong parent, because the
bold/numbering heuristic no longer holds. I would catch this by (a)
keeping the irregularity-focused unit tests as a regression suite
whenever the source document changes, and (b) adding a sanity check
that flags documents where an unusually large fraction of body text
ends up attached to very few nodes (a signature of headings being
missed).

**2. Where did you choose simplicity over correctness because of time,
and what would break first if this went to production as-is?**

Version-matching by `numbering` string instead of a fuzzy/robust
content-based matcher (section 3). This would break first: any
document restructuring (inserting a section that shifts later
numbering) would misreport genuinely-unchanged content as
removed+added, generating unnecessary staleness alarms and confusing
whoever is triaging them. In production I'd want at minimum a fallback
match by (heading text + body similarity) when the exact numbering
isn't found, before concluding "removed."

**3. Name one input you did not handle, and what your system does
when it sees it.**

A PDF where **no lines are ever detected as bold** (e.g. a manual
scanned as an image with no embedded font/style metadata at all, or a
PDF where the source document simply doesn't use bold for headings).
In that case, `is_heading_line` would never return a match, and
`build_tree` would silently produce a single root-level node
containing the *entire* document as body text, with no crash and no
error - exactly the "looks clean but is quietly wrong" failure the
assignment warns about. I did not add OCR-based or layout-based
fallback heading detection for this case; a production version would
need a secondary signal (e.g. whitespace/line-gap analysis, or
requiring human confirmation of at least one detected heading before
trusting the whole parse).

## 7. What I'd do differently with more time

- Add a fuzzy version-matching fallback (heading + body similarity)
  for when a section's numbering doesn't match between versions, to
  handle document restructuring gracefully.
- Distinguish cosmetic/wording-only changes from substantive changes
  in the staleness check (e.g. by diffing normalized/whitespace-
  collapsed text, or specifically flagging numeric-value changes as
  higher severity).
- Move the generated-test-case store to a real MongoDB instance if the
  system needed to scale past a single-file store, or needed
  concurrent-write safety (the current JSON file has no locking).
- Add a lightweight admin/debug endpoint to re-run the irregularity
  unit tests against a newly-uploaded PDF before committing to
  ingesting it as a real version.
