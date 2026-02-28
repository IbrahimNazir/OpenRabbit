# ADR-0010: Custom Unified Diff Parser over Third-Party Libraries

| Field | Value |
|-------|-------|
| **ID** | ADR-0010 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 2 — GitHub Client & Diff Fetching |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | diff, parsing, core, github-api |

---

## Context and Problem Statement

Every PR review begins by parsing GitHub's unified diff format into structured data. The parser must produce: file-level metadata (filename, status, language), hunk-level data (line ranges, added/removed/context lines), and — critically — the **diff position** for every line.

The diff position is GitHub's 1-indexed line counter within the entire diff for a file, starting from the first hunk header. It is not the same as the line number in the new file. Getting this wrong means every inline PR comment is posted at the wrong location (GitHub's API returns 422 Unprocessable Entity for invalid positions).

---

## Decision Drivers

1. **Diff position accuracy** — Must produce exactly the position value that GitHub's Review API expects. This is OpenRabbit's most critical correctness requirement for the core user experience.
2. **Function context extraction** — Git diffs sometimes include a function name after `@@` (e.g., `@@ -10,5 +10,7 @@ def process_payment():`). We need this for enriching hunk context.
3. **Edge case handling** — Binary files, renamed files, files with no newline at end, empty diffs, added/deleted files (which have no "old" path).
4. **Zero external dependency** — The diff parser is our most foundational component. External library bugs directly block all reviews.
5. **Testability** — Must be testable in isolation with a comprehensive fixture library.

---

## Considered Options

### Option A: Custom Parser (CHOSEN)

Write our own parser of ~150 lines. Parse the unified diff format line by line, maintaining a `diff_position` counter that increments for every line that is not a file header.

**Pros:** Full control over position calculation, function context extraction, error handling
**Cons:** Must be written and tested thoroughly

### Option B: `unidiff` library (PyPI)

```python
from unidiff import PatchSet
patch = PatchSet(diff_text)
```

**Problems:**
- `unidiff` does not expose the raw `diff_position` as GitHub defines it — it exposes `source_line_no`, `target_line_no`, `diff_line_no` but the semantics differ from what GitHub's API requires
- The library maps positions differently for multi-hunk files — our internal testing found position off-by-one errors when a diff has multiple hunks per file
- No function context extraction from the `@@` header
- Adding a dependency for something we can write in 150 lines adds a version maintenance burden and a potential security surface
- **Rejected:** position calculation semantics are wrong for GitHub's API

### Option C: `whatthepatch` library

Similar to `unidiff` — provides parsed hunks but not GitHub-compatible positions. Rejected for the same reason.

### Option D: Use GitHub's API to get file changes directly

Instead of parsing the diff text, call `GET /repos/{owner}/{repo}/pulls/{pr}/files` to get structured file change data including `changes`, `additions`, `deletions` per file.

**Problems:**
- This API returns file-level metadata but not hunk-level data — we lose the ability to do hunk-level analysis (which hunks were changed, in which function)
- The `patch` field in the response is the same unified diff format, so we'd still need to parse it
- No position data — the GitHub Pulls Files API doesn't return the diff position either; only the raw patch text
- **Rejected:** doesn't solve the problem and adds an extra API call

---

## Decision

**Implement a custom unified diff parser in `app/core/diff_parser.py`.**

### Diff Position Calculation (Critical)

The diff position follows these rules — this is the exact algorithm GitHub uses:

```python
def compute_diff_positions(diff_text: str) -> dict[str, dict[int, int]]:
    """
    Returns: { filename: { new_file_line_number: diff_position } }
    
    diff_position rules:
    - Reset to 0 for each new file (diff --git a/... b/...)
    - The @@ hunk header line itself counts as position 1 for that hunk
    - Each subsequent line (context, added, removed) increments position by 1
    - Removed lines (-) increment position but have no new_lineno mapping
    - Context lines and added lines (+) both increment position
    - Only added lines and context lines can have PR comments (not removed lines)
    
    CRITICAL: diff_position does NOT reset between hunks — it is cumulative
    within the entire file diff.
    """
    positions = {}
    current_file = None
    diff_pos = 0
    new_line = 0
    
    for line in diff_text.split('\n'):
        if line.startswith('diff --git'):
            # New file — extract filename, reset position counter
            current_file = extract_filename(line)
            positions[current_file] = {}
            diff_pos = 0
            
        elif line.startswith('@@') and current_file:
            # Hunk header — increment position, update new_line counter
            diff_pos += 1
            match = re.search(r'\+(\d+)(?:,\d+)?', line)
            new_line = int(match.group(1)) - 1  # will be incremented on first line
            
        elif current_file and diff_pos > 0:
            diff_pos += 1
            if line.startswith('+'):
                new_line += 1
                positions[current_file][new_line] = diff_pos
            elif line.startswith('-'):
                pass  # removed lines don't increment new_line
            else:  # context line (starts with ' ')
                new_line += 1
                positions[current_file][new_line] = diff_pos
    
    return positions
```

### Complete Data Model

```python
@dataclass
class DiffLine:
    content: str
    line_type: Literal['added', 'removed', 'context']
    old_lineno: int | None   # None for added lines
    new_lineno: int | None   # None for removed lines
    diff_position: int       # GitHub's 1-indexed position within the file diff

@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str              # Raw @@ line including optional function context
    function_context: str | None  # e.g., "def process_payment():" extracted from header
    lines: list[DiffLine]

@dataclass
class FileDiff:
    filename: str            # New filename (after rename if applicable)
    old_filename: str | None # Old filename before rename, None if not renamed
    status: Literal['added', 'modified', 'removed', 'renamed']
    language: str | None     # Detected from extension
    hunks: list[DiffHunk]
    additions: int           # Total lines added
    deletions: int           # Total lines removed
    is_binary: bool = False  # True for binary files — skip review

# Derived utility function — the most critical function for comment posting
def build_line_to_position_map(file_diff: FileDiff) -> dict[int, int]:
    """
    Returns { new_file_line_number: diff_position } for all commentable lines.
    Only added (+) and context lines are commentable — removed lines are not.
    """
    return {
        line.new_lineno: line.diff_position
        for hunk in file_diff.hunks
        for line in hunk.lines
        if line.line_type in ('added', 'context') and line.new_lineno is not None
    }
```

### Language Detection

```python
EXTENSION_TO_LANGUAGE = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.go': 'go',
    '.rs': 'rust',
    '.java': 'java',
    '.kt': 'kotlin',
    '.swift': 'swift',
    '.rb': 'ruby',
    '.php': 'php',
    '.cs': 'csharp',
    '.cpp': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.sh': 'bash',
    '.sql': 'sql',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.json': 'json',
    '.tf': 'terraform',
    '.proto': 'protobuf',
}
```

---

## Consequences

### Positive
- Complete control over position calculation — we own the algorithm
- Can add function context extraction with zero library changes
- No external dependency that could change its behavior between versions
- Parser is a pure function: `parse_diff(str) -> list[FileDiff]` — trivially testable

### Negative
- We own the bug surface. **Mitigation:** 10+ test fixtures covering all edge cases (new file, deleted file, rename, binary, multi-hunk, no-newline-at-end, empty diff), run in CI on every commit

### Mandatory Test Fixtures

```
tests/fixtures/diffs/
├── simple_modification.diff      # 1 file, 1 hunk, basic add/remove
├── new_file.diff                 # added file (no old path, all lines are additions)
├── deleted_file.diff             # removed file (all lines are deletions)
├── renamed_file.diff             # rename with modifications
├── multi_hunk.diff               # 1 file, 3 hunks far apart
├── adjacent_hunks.diff           # 2 hunks within 5 lines of each other
├── multi_file.diff               # 4 files changed in one diff
├── binary_file.diff              # binary file (should be skipped)
├── no_newline.diff               # "\ No newline at end of file" marker
├── function_context.diff         # @@ header includes function name
└── empty_diff.diff               # PR with no changes (edge case)
```
