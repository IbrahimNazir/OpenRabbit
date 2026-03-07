"""Semantic code chunk extractor for RAG indexing.

Implements ADR-0021: three-tier chunking strategy.

Tier 1 — Tree-sitter semantic boundaries (function/class nodes).
Tier 2 — tiktoken sliding window fallback (512 tokens, 128 overlap).
Tier 3 — File-level chunk (first 50 lines, always included).

Usage:
    chunks = extract_chunks(file_content, "src/auth/login.py")
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.parsing.tree_sitter_parser import TreeSitterParser

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Language detection (subset — full mapping is in diff_parser.py)
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
}

# Sliding-window constants
SLIDING_WINDOW_TOKENS = 512
SLIDING_WINDOW_OVERLAP = 128
LARGE_FUNCTION_LINE_THRESHOLD = 100
FILE_LEVEL_LINES = 50

# Module-level parser singleton
_parser = TreeSitterParser()


@dataclass
class CodeChunk:
    """A logical unit of code extracted from a source file."""

    chunk_id: str        # SHA256(file_path + name + start_line)[:16]
    file_path: str
    chunk_type: str      # "function" | "class" | "file_header" | "window"
    name: str            # function/class name or "<file_header>" or "<window_N>"
    content: str
    start_line: int      # 1-indexed
    end_line: int        # 1-indexed (inclusive)
    language: str


def _make_chunk_id(file_path: str, name: str, start_line: int) -> str:
    raw = f"{file_path}:{name}:{start_line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _detect_language(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(suffix, "text")


# ---------------------------------------------------------------------------
#  Public entry point
# ---------------------------------------------------------------------------


def extract_chunks(file_content: str, file_path: str) -> list[CodeChunk]:
    """Extract semantic code chunks from *file_content*.

    Always includes a file-level chunk (first 50 lines).  Attempts Tree-sitter
    semantic chunking; falls back to sliding-window if unavailable.

    Args:
        file_content: Full file text.
        file_path: Repository-relative path (used for language detection and IDs).

    Returns:
        List of ``CodeChunk`` objects, deduplicated by chunk_id.
    """
    if not file_content or not file_content.strip():
        return []

    language = _detect_language(file_path)
    lines = file_content.splitlines()
    chunks: list[CodeChunk] = []

    # Tier 3 — always add a file header chunk
    header_lines = lines[:FILE_LEVEL_LINES]
    if header_lines:
        chunks.append(
            CodeChunk(
                chunk_id=_make_chunk_id(file_path, "<file_header>", 1),
                file_path=file_path,
                chunk_type="file_header",
                name="<file_header>",
                content="\n".join(header_lines),
                start_line=1,
                end_line=min(FILE_LEVEL_LINES, len(lines)),
                language=language,
            )
        )

    # Try Tier 1 — semantic chunking via Tree-sitter
    tree = _parser.parse_file(file_content, language)
    if tree is not None:
        semantic_chunks = _extract_semantic_chunks(
            tree, file_content, lines, file_path, language
        )
        chunks.extend(semantic_chunks)
    else:
        # Tier 2 — sliding-window fallback
        window_chunks = _extract_sliding_window(lines, file_path, language)
        chunks.extend(window_chunks)

    # Deduplicate by chunk_id (file_header + first semantic chunk may overlap)
    seen: set[str] = set()
    unique: list[CodeChunk] = []
    for chunk in chunks:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            unique.append(chunk)

    return unique


# ---------------------------------------------------------------------------
#  Tier 1 — Semantic (Tree-sitter)
# ---------------------------------------------------------------------------


def _extract_semantic_chunks(
    tree: object,
    file_content: str,
    lines: list[str],
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    nodes = _parser.extract_function_nodes(tree, file_content, language)
    chunks: list[CodeChunk] = []

    for node in nodes:
        # tree-sitter returns 0-indexed lines; convert to 1-indexed
        start_line = node["start_line"] + 1
        end_line = node["end_line"] + 1
        name = node["name"] or "<anonymous>"
        node_type = node["node_type"]
        chunk_type = "class" if "class" in node_type else "function"

        line_count = end_line - start_line + 1

        if line_count <= LARGE_FUNCTION_LINE_THRESHOLD:
            content = "\n".join(lines[start_line - 1 : end_line])
            chunks.append(
                CodeChunk(
                    chunk_id=_make_chunk_id(file_path, name, start_line),
                    file_path=file_path,
                    chunk_type=chunk_type,
                    name=name,
                    content=content,
                    start_line=start_line,
                    end_line=end_line,
                    language=language,
                )
            )
        else:
            # Split large functions at blank-line boundaries with 5-line overlap
            sub_chunks = _split_large_function(
                lines, file_path, language, name, start_line, end_line
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_large_function(
    lines: list[str],
    file_path: str,
    language: str,
    func_name: str,
    start_line: int,
    end_line: int,
    overlap: int = 5,
) -> list[CodeChunk]:
    """Split a large function into sub-chunks at blank-line boundaries."""
    func_lines = lines[start_line - 1 : end_line]
    chunks: list[CodeChunk] = []
    sub_idx = 0
    chunk_start = 0  # 0-indexed within func_lines

    while chunk_start < len(func_lines):
        # Find the end of this sub-chunk: up to LARGE_FUNCTION_LINE_THRESHOLD lines
        chunk_end = min(chunk_start + LARGE_FUNCTION_LINE_THRESHOLD, len(func_lines))

        # Prefer to break at a blank line
        if chunk_end < len(func_lines):
            for i in range(chunk_end, max(chunk_start, chunk_end - 20), -1):
                if not func_lines[i - 1].strip():
                    chunk_end = i
                    break

        sub_content = "\n".join(func_lines[chunk_start:chunk_end])
        abs_start = start_line + chunk_start
        abs_end = start_line + chunk_end - 1
        sub_name = f"{func_name}__part{sub_idx}"

        chunks.append(
            CodeChunk(
                chunk_id=_make_chunk_id(file_path, sub_name, abs_start),
                file_path=file_path,
                chunk_type="function",
                name=sub_name,
                content=sub_content,
                start_line=abs_start,
                end_line=abs_end,
                language=language,
            )
        )

        sub_idx += 1
        # Overlap: start next chunk `overlap` lines before the end
        chunk_start = max(chunk_end - overlap, chunk_start + 1)

    return chunks


# ---------------------------------------------------------------------------
#  Tier 2 — Sliding window (tiktoken)
# ---------------------------------------------------------------------------


def _extract_sliding_window(
    lines: list[str],
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    """Fallback chunking: fixed token-size windows with overlap."""
    try:
        import tiktoken  # type: ignore[import]

        enc = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        logger.warning("tiktoken not installed — using line-count fallback")
        return _extract_line_window(lines, file_path, language)

    chunks: list[CodeChunk] = []
    all_text = "\n".join(lines)
    tokens = enc.encode(all_text)

    if not tokens:
        return chunks

    win = SLIDING_WINDOW_TOKENS
    step = win - SLIDING_WINDOW_OVERLAP
    win_idx = 0
    token_pos = 0

    while token_pos < len(tokens):
        end_pos = min(token_pos + win, len(tokens))
        chunk_tokens = tokens[token_pos:end_pos]
        chunk_text = enc.decode(chunk_tokens)

        # Determine start line from character offset (approximate)
        prefix_text = enc.decode(tokens[:token_pos])
        start_line = prefix_text.count("\n") + 1
        end_line = start_line + chunk_text.count("\n")

        chunks.append(
            CodeChunk(
                chunk_id=_make_chunk_id(file_path, f"<window_{win_idx}>", start_line),
                file_path=file_path,
                chunk_type="window",
                name=f"<window_{win_idx}>",
                content=chunk_text,
                start_line=start_line,
                end_line=end_line,
                language=language,
            )
        )

        win_idx += 1
        token_pos += step

    return chunks


def _extract_line_window(
    lines: list[str],
    file_path: str,
    language: str,
) -> list[CodeChunk]:
    """Ultra-simple fallback: 100-line windows with 20-line overlap."""
    chunks: list[CodeChunk] = []
    win = 100
    overlap = 20
    step = win - overlap
    win_idx = 0
    pos = 0

    while pos < len(lines):
        end = min(pos + win, len(lines))
        content = "\n".join(lines[pos:end])
        start_line = pos + 1
        end_line = end

        chunks.append(
            CodeChunk(
                chunk_id=_make_chunk_id(file_path, f"<window_{win_idx}>", start_line),
                file_path=file_path,
                chunk_type="window",
                name=f"<window_{win_idx}>",
                content=content,
                start_line=start_line,
                end_line=end_line,
                language=language,
            )
        )

        win_idx += 1
        pos += step

    return chunks
