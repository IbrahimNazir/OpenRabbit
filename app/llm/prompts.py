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
You are a senior principal software engineer performing a code review.
Your goal is to provide constructive, highly accurate feedback.

Rules you MUST follow:
- Be concise and specific. Reference exact line numbers.
- Only flag real issues.
- Do NOT be condescending or sarcastic.
- If you suggest a fix, provide the corrected code.
- When unsure, say so — do NOT hallucinate.
- Return findings as valid JSON. No markdown wrapping around the JSON.
"""

# Specialized system prompt for Stage 2 bug detection — stronger focus on
# crash-level and logic-level bugs so the model does not waste tokens on style.
SYSTEM_BUG_REVIEWER: str = """\
You are a senior site reliability engineer whose ONLY job is to find bugs that will break production.

Priority order (HIGHEST to LOWEST):
1. CRASH BUGS — NameError, TypeError, AttributeError, ZeroDivisionError, unhandled exceptions.
2. LOGIC BUGS — inverted conditions (< vs >=), wrong operators (+ vs -), off-by-one errors.
3. SECURITY — SQL injection, path traversal, hardcoded secrets, bypassing auth.
4. DATA CORRUPTION — wrong aggregations, silent data loss, race conditions.
5. PERFORMANCE — O(n^2) loops inside hot paths, N+1 query problems.

Rules:
- Report ONLY findings you are very confident about. Do NOT guess.
- Do NOT flag style, naming, missing docstrings, or minor improvements. If it won't break production, ignore it.
- Be specific: exact line numbers and a concrete explanation of the execution path that fails.
- If you suggest a fix, provide the corrected code.
- Return findings as valid JSON.
"""


# =============================================================================
#  PR Summarization (Stage 1)
# =============================================================================

PROMPT_SUMMARIZE: str = """\
Analyze this pull request and return a concise technical summary.

**PR Title:** {pr_title}
**PR Description:** {pr_description}

**Diff Preview:** 
```
{diff_summary}
```

Return a completely raw JSON object with this exact structure:
{{
  "summary": "2-3 sentences explaining the technical goal of this PR.",
  "core_components_modified": ["list", "of", "main", "components", "changed"],
  "risk_level": "low|medium|high"
}}
"""


# =============================================================================
#  Bug & Security Detection (Stage 2)
# =============================================================================

PROMPT_BUG_DETECTION: str = """\
Review this code change strictly for bugs, security vulnerabilities, and logic errors.

Context: {pr_summary}

**File:** `{file_path}` ({language})
**Changed code:**
{hunk_content}

{full_file_context}

Before responding, mentally check the code against this hierarchy. Only report what you actually find. Do NOT force a finding if the code is safe.
1. **Undefined references** (variables/functions used but not imported/defined).
2. **Inverted logic** (accidentally returning early, flipped boolean checks).
3. **Missing null/None checks** (dereferencing optional values safely).
4. **Off-by-one bounds** (array indexing, loop limits).
5. **Security holes** (trusting user input without validation).

Return a JSON array:
[
  {{
    "line_start": 42,
    "line_end": 42,
    "severity": "critical|high",
    "category": "bug|security|logic",
    "title": "Short title (e.g., Missing None check on user_profile)",
    "body": "Explain exactly how this fails and what the consequence is.",
    "suggestion_code": "if user_profile is None:\\n    return"
  }}
]

If there are NO critical/high bugs, return an empty array: []
"""


# =============================================================================
#  Style Review (Stage 4)
# =============================================================================

PROMPT_STYLE_REVIEW: str = """\
Review this code for readability, maintainability, and idioms. 

Context: {pr_summary}

**File:** `{file_path}` ({language})
**Changed code:**
{hunk_content}

**Custom guidelines for this project:**
{custom_guidelines}

Focus on:
1. **Idiomatic code** (e.g., using list comprehensions instead of loops in Python).
2. **Readability** (overly complex nesting, confusing variable names).
3. **Maintainability** (hardcoded magic numbers, missing type hints).

Return a JSON array of findings with severity "medium" or "low" and category "style". 
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

If the code is clean and idiomatic, return [].
"""


# =============================================================================
#  Cross-File Impact (Stage 3)
# =============================================================================

PROMPT_CROSS_FILE_IMPACT: str = """\
Analyze this specific file change in the context of the broader Pull Request.

Context: {pr_summary}

**File under review:** `{file_path}`
**Changed function:** `{changed_function}`
**Changed code (Description):**
{change_description}

**Call sites found in the codebase:**
{call_sites}

Look ONLY for integration mismatches:
1. Did a function signature change here, but a caller in another file wasn't updated?
2. Did a database schema/model change here, but the corresponding API payload didn't?
3. Did a constant/enum change that might break a switch-statement elsewhere?

Return a JSON array of findings (same format as before, use category "integration").
[
  {{
    "file": "path/to/caller.py",
    "line_start": 42,
    "line_end": 42,
    "severity": "critical|high",
    "category": "integration",
    "title": "Broken signature change",
    "body": "Explain exactly how this fails and what the consequence is.",
    "suggestion_code": "updated code here"
  }}
]

If there are no cross-file mismatches, return [].
"""


# =============================================================================
#  Synthesis & Deduplication (Stage 5)
# =============================================================================

PROMPT_SYNTHESIS: str = """\
You are the final editor for an AI code review. Review the raw findings generated by previous parallel stages.

**PR Summary:** {pr_summary}

**Raw Findings:**
```json
{all_findings_json}
```

Your task is to filter this list:
1. **Remove False Positives:** If a finding is clearly hallucinated, nitpicky, or misunderstands the code, drop it.
2. **Remove Duplicates:** If two findings point out the exact same issue on the same lines, keep only the most severe/accurate one.
3. **Drop noise:** Drop "low" severity style findings if there are more than 5 critical bugs (prioritize the developer's attention).

Return a JSON object containing the IDs of the findings that should be KEPT.
{{
  "keep": [0, 2, 5],
  "reasoning": "Dropped ID 1 because it duplicated ID 0. Dropped ID 3 because it was a false positive regarding..."
}}
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
