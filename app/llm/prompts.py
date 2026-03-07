"""Prompt templates for the OpenRabbit AI review pipeline.

Each template is a string constant with ``{placeholder}`` markers that are
filled at runtime by the pipeline stages.  Templates follow the structure
from the 20-day build plan Section 4, Task 3.3.
"""

from __future__ import annotations


# =============================================================================
#  System prompts
# =============================================================================

SYSTEM_REVIEWER: str = """\
You are a senior software engineer performing a code review on a pull request.

Rules you MUST follow:
- Be concise and specific. Reference exact line numbers.
- Only flag real issues: bugs, security vulnerabilities, logic errors, and \
  significant performance problems.
- Do NOT be condescending or sarcastic.
- If you suggest a fix, provide the corrected code.
- When unsure, say so — do not hallucinate.
- Return findings as valid JSON. No markdown wrapping around the JSON.
"""

# Specialized system prompt for Stage 2 bug detection — stronger focus on
# crash-level and logic-level bugs so the model does not waste tokens on style.
SYSTEM_BUG_REVIEWER: str = """\
You are a senior software engineer whose ONLY job is to find bugs that will \
break production code.

Priority order (HIGHEST to LOWEST):
1. CRASH BUGS — NameError, TypeError, AttributeError, ZeroDivisionError, \
   IndexError, KeyError, any unhandled exception.
2. LOGIC BUGS — inverted conditions, wrong operators (+ vs −), off-by-one, \
   swapped arguments.
3. SECURITY — SQL injection, path traversal, hardcoded secrets, missing auth.
4. DATA CORRUPTION — incorrect calculations, wrong totals, silent data loss.
5. PERFORMANCE — only if clearly O(n²) or worse on a hot path.

Rules:
- Report ONLY findings you are very confident about. Do NOT guess.
- Do NOT flag style, naming, missing docstrings, or minor improvements.
- Be specific: exact line numbers and a concrete explanation of what goes wrong.
- If you suggest a fix, provide the corrected code.
- Return findings as valid JSON. No markdown wrapping.
"""


# =============================================================================
#  PR Summarization (Stage 1)
# =============================================================================

PROMPT_SUMMARIZE: str = """\
Analyze this pull request and return a concise summary.

**PR Title:** {pr_title}
**PR Description:** {pr_description}

**Diff (first 2000 chars):**
```
{diff_summary}
```

Return a JSON object with EXACTLY this structure:
{{
  "summary": "One paragraph describing what this PR does and why.",
  "key_changes": ["change 1", "change 2", "change 3"],
  "risk_level": "low|medium|high"
}}

Example good response:
{{
  "summary": "Adds user authentication via JWT tokens, including login/logout endpoints and middleware for protected routes.",
  "key_changes": ["New /auth/login endpoint", "JWT middleware added to protected routes", "User model updated with password hash field"],
  "risk_level": "high"
}}
"""


# =============================================================================
#  Bug & Security Detection (Stage 2)
# =============================================================================

PROMPT_BUG_DETECTION: str = """\
Review this code change for bugs, security issues, and logic errors.

**File:** `{file_path}` ({language})
**Changed code (with line numbers):**
```{language}
{hunk_content}
```

{full_file_context}

Before writing your response, mentally check the code for each of these bug
categories. Only report what you actually find — do NOT hallucinate issues.

- **Undefined names** — variable, function, or constant used but never defined
  or imported. Will cause NameError/AttributeError at runtime.
- **Inverted conditions** — comparison operators pointing the wrong way. Trace
  through with sample values to verify.
- **Wrong arithmetic operators** — using + instead of −, or * instead of /.
- **Division by zero** — dividing by a value that might be zero.
- **Off-by-one errors** — wrong loop bounds or slice indices.
- **Unhandled None** — `.get()` may return None used unsafely afterwards.
- **Security** — injection, path traversal, hardcoded secrets, missing auth.

Return a JSON array of findings. Each finding:
[
  {{
    "line_start": 42,
    "line_end": 42,
    "severity": "critical|high|medium|low",
    "category": "bug|security|performance|logic",
    "title": "Short title of the issue",
    "body": "Detailed explanation of the problem and why it matters.",
    "suggestion_code": "corrected code here, or null if no fix"
  }}
]

If there are NO issues, return an empty array: []

Severity guide:
- **critical**: Will crash at runtime (NameError, ZeroDivisionError, TypeError)
  or cause data loss / security breach.
- **high**: Silently produces wrong results (wrong operator, inverted condition).
- **medium**: Edge-case bug that triggers under certain conditions.
- **low**: Minor improvement — only report if no higher-severity issues exist.

Do NOT report style issues, missing docstrings, or naming preferences.
"""


# =============================================================================
#  Style Review (Stage 4)
# =============================================================================

PROMPT_STYLE_REVIEW: str = """\
Review this code for style issues and best practices.

**File:** `{file_path}` ({language})
**Changed code:**
```{language}
{hunk_content}
```

**Custom guidelines for this project:**
{custom_guidelines}

Return a JSON array of findings (same format as bug detection but category='style'):
[
  {{
    "line_start": 10,
    "line_end": 10,
    "severity": "low",
    "category": "style",
    "title": "Missing docstring",
    "body": "Public function lacks a docstring. Consider adding one.",
    "suggestion_code": null
  }}
]

If there are NO style issues, return an empty array: []
Only flag issues at severity 'low' or 'medium'. Never 'critical' or 'high' for style.
"""


# =============================================================================
#  Cross-File Impact (Stage 3)
# =============================================================================

PROMPT_CROSS_FILE_IMPACT: str = """\
A function was changed in a pull request. Determine if this breaks any call sites.

**Changed function:** `{changed_function}`
**Change description:** {change_description}

**Call sites found in the codebase:**
{call_sites}

Return a JSON object:
{{
  "has_breaking_changes": true,
  "affected_call_sites": [
    {{
      "file": "path/to/file.py",
      "line": 42,
      "issue": "Description of the breaking change",
      "suggestion": "How to fix the call site"
    }}
  ]
}}

If there are NO breaking changes, return:
{{
  "has_breaking_changes": false,
  "affected_call_sites": []
}}
"""


# =============================================================================
#  Synthesis & Deduplication (Stage 5)
# =============================================================================

PROMPT_SYNTHESIS: str = """\
You are given a list of code review findings. Remove duplicates and false positives.

**PR Summary:** {pr_summary}

**All findings:**
```json
{all_findings_json}
```

Return a JSON object with:
{{
  "keep": [0, 2, 5],
  "remove_duplicates": [1, 3],
  "false_positives": [4],
  "final_summary": "Updated summary incorporating the review findings."
}}

Rules:
- If two findings cover the same lines or same issue, keep only the higher severity one.
- Remove findings that are clearly false positives.
- Cap at 25 findings maximum.
"""


# =============================================================================
#  Natural Language Change Description (RAG query construction — Phase 3)
# =============================================================================

PROMPT_DESCRIBE_CHANGE: str = (
    "Describe in one sentence what this code change does:\n\n"
    "```{language}\n{hunk_content}\n```\n\n"
    "Return ONLY the one-sentence description. No JSON, no explanation."
)


# =============================================================================
#  Fix This (Conversation)
# =============================================================================

PROMPT_FIX_THIS: str = """\
A developer asked you to fix an issue you found in their code.

**Original finding:** {original_finding}
**File:** `{file_path}`
**Lines {line_start}-{line_end} of the current file:**
```
{file_content}
```

Return a JSON object:
{{
  "fixed_code": "the corrected code for lines {line_start}-{line_end} only",
  "explanation": "Brief explanation of what was changed and why."
}}

The fixed_code MUST be a drop-in replacement for the specified lines.
Do NOT include line numbers in the fixed_code.
"""
