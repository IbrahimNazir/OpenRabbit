"""Tree-sitter AST parser for multi-language code analysis.

Implements ADR-0020: language-specific tree-sitter packages, in-process
parsing, cached Parser instances.

Supports: python, javascript, typescript, go, rust, java.
Unsupported languages return None from get_parser() — callers fall back
to sliding-window chunking.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Lazy import helpers — tree-sitter and language packages are optional
#  dependencies; if not installed the module still loads but returns None
#  from get_parser().
# ---------------------------------------------------------------------------

try:
    from tree_sitter import Language, Node, Parser, Tree  # type: ignore[import]

    _TS_AVAILABLE = True
except ImportError:  # pragma: no cover
    Language = None  # type: ignore[misc, assignment]
    Node = None  # type: ignore[misc, assignment]
    Parser = None  # type: ignore[misc, assignment]
    Tree = None  # type: ignore[misc, assignment]
    _TS_AVAILABLE = False
    logger.warning("tree-sitter not installed — AST parsing disabled")


def _load_language(name: str) -> Any | None:
    """Attempt to load a tree-sitter language binding.  Returns None on failure."""
    if not _TS_AVAILABLE:
        return None
    try:
        if name == "python":
            import tree_sitter_python as m  # type: ignore[import]

            return Language(m.language())
        if name == "javascript":
            import tree_sitter_javascript as m  # type: ignore[import]

            return Language(m.language())
        if name in ("typescript", "tsx"):
            import tree_sitter_typescript as m  # type: ignore[import]

            # The typescript package exposes both .language_typescript() and
            # .language_tsx() depending on the variant.
            if name == "tsx":
                fn = getattr(m, "language_tsx", None) or getattr(m, "language", None)
            else:
                fn = getattr(m, "language_typescript", None) or getattr(m, "language", None)
            return Language(fn())
        if name == "go":
            import tree_sitter_go as m  # type: ignore[import]

            return Language(m.language())
        if name == "rust":
            import tree_sitter_rust as m  # type: ignore[import]

            return Language(m.language())
        if name == "java":
            import tree_sitter_java as m  # type: ignore[import]

            return Language(m.language())
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to load tree-sitter language %s: %s", name, exc)
    return None


# Module-level caches — built lazily on first call.
_LANGUAGE_CACHE: dict[str, Any] = {}
_PARSER_CACHE: dict[str, Any] = {}

# Languages that map to the same underlying grammar.
_LANGUAGE_ALIASES: dict[str, str] = {
    "jsx": "javascript",
    "tsx": "tsx",
}

# Per-language node types that represent function / class definitions
# used by extract_function_nodes() and get_enclosing_function().
_FUNCTION_NODE_TYPES: dict[str, set[str]] = {
    "python": {
        "function_definition",
        "async_function_definition",
        "class_definition",
    },
    "javascript": {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class_expression",
    },
    "typescript": {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "method_signature",
        "function_signature",
    },
    "tsx": {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
    },
    "go": {
        "function_declaration",
        "method_declaration",
        "func_literal",
    },
    "rust": {
        "function_item",
        "closure_expression",
        "impl_item",
    },
    "java": {
        "method_declaration",
        "constructor_declaration",
        "class_declaration",
        "interface_declaration",
    },
}

# Node type names that carry a "name" child field.
_NAME_FIELD = "name"

# Import node types per language.
_IMPORT_NODE_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement", "import_declaration"},
    "typescript": {"import_statement", "import_declaration"},
    "tsx": {"import_statement", "import_declaration"},
    "go": {"import_declaration", "import_spec"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
}


class TreeSitterParser:
    """Multi-language AST parser backed by tree-sitter.

    All methods are synchronous and CPU-bound (safe to call from async code
    via ``loop.run_in_executor`` for large files, but typically fast enough
    to call directly for files < 5 000 lines).
    """

    # ------------------------------------------------------------------
    #  Parser retrieval
    # ------------------------------------------------------------------

    def get_parser(self, language: str) -> Any | None:
        """Return a cached tree-sitter Parser for *language*, or None.

        Args:
            language: Language name (e.g. ``"python"``, ``"typescript"``).

        Returns:
            A ``tree_sitter.Parser`` instance, or ``None`` if the language
            is unsupported or the package is not installed.
        """
        lang_key = _LANGUAGE_ALIASES.get(language, language)

        if lang_key in _PARSER_CACHE:
            return _PARSER_CACHE[lang_key]

        lang_obj = _LANGUAGE_CACHE.get(lang_key)
        if lang_obj is None:
            lang_obj = _load_language(lang_key)
            _LANGUAGE_CACHE[lang_key] = lang_obj  # cache even if None

        if lang_obj is None:
            _PARSER_CACHE[lang_key] = None
            return None

        if not _TS_AVAILABLE:  # pragma: no cover
            return None

        parser = Parser(lang_obj)
        _PARSER_CACHE[lang_key] = parser
        return parser

    # ------------------------------------------------------------------
    #  Parsing
    # ------------------------------------------------------------------

    def parse_file(self, source_code: str, language: str) -> Any | None:
        """Parse *source_code* and return a tree-sitter ``Tree``, or None.

        Args:
            source_code: Full file content as a string.
            language: Language name.

        Returns:
            ``tree_sitter.Tree`` or ``None`` if parsing is unavailable.
        """
        parser = self.get_parser(language)
        if parser is None:
            return None
        try:
            return parser.parse(source_code.encode("utf-8", errors="replace"))
        except Exception as exc:
            logger.warning("Tree-sitter parse failed for %s: %s", language, exc)
            return None

    # ------------------------------------------------------------------
    #  Function / class node extraction
    # ------------------------------------------------------------------

    def extract_function_nodes(
        self, tree: Any, source_code: str, language: str
    ) -> list[dict[str, Any]]:
        """Extract function and class definitions from a parsed AST.

        Args:
            tree: A ``tree_sitter.Tree`` returned by ``parse_file()``.
            source_code: The original source (used for extracting names).
            language: Language name (used to select the right node types).

        Returns:
            List of dicts with keys:
            ``name``, ``start_line``, ``end_line``, ``start_byte``,
            ``end_byte``, ``node_type``.
        """
        lang_key = _LANGUAGE_ALIASES.get(language, language)
        target_types = _FUNCTION_NODE_TYPES.get(lang_key, set())
        if not target_types:
            return []

        results: list[dict[str, Any]] = []
        self._walk_for_types(tree.root_node, target_types, source_code, results)
        return results

    def _walk_for_types(
        self,
        node: Any,
        target_types: set[str],
        source_code: str,
        results: list[dict[str, Any]],
    ) -> None:
        """Recursively walk AST nodes, collecting those in *target_types*."""
        if node.type in target_types:
            name = self._extract_node_name(node, source_code)
            results.append(
                {
                    "name": name,
                    "start_line": node.start_point[0],  # 0-indexed
                    "end_line": node.end_point[0],       # 0-indexed
                    "start_byte": node.start_byte,
                    "end_byte": node.end_byte,
                    "node_type": node.type,
                }
            )
            # Still recurse into nested classes/functions
        for child in node.children:
            self._walk_for_types(child, target_types, source_code, results)

    def _extract_node_name(self, node: Any, source_code: str) -> str:
        """Extract the identifier name from a definition node."""
        # Try the "name" field first (most languages)
        name_node = node.child_by_field_name(_NAME_FIELD)
        if name_node is not None:
            return source_code[name_node.start_byte : name_node.end_byte]

        # Arrow functions may not have a name field — use a placeholder
        if node.type == "arrow_function":
            return "<arrow>"

        # Fallback: first identifier child
        for child in node.children:
            if child.type == "identifier":
                return source_code[child.start_byte : child.end_byte]

        return "<anonymous>"

    # ------------------------------------------------------------------
    #  Enclosing function lookup
    # ------------------------------------------------------------------

    def get_enclosing_function(
        self, tree: Any, line_number: int, source_code: str, language: str
    ) -> str | None:
        """Return the name of the innermost function containing *line_number*.

        Args:
            tree: Parsed tree-sitter Tree.
            line_number: 1-indexed new file line number (as used in hunks).
            source_code: Original source.
            language: Language name.

        Returns:
            Function/method name string, or ``None`` if not inside any.
        """
        # Convert to 0-indexed for tree-sitter
        zero_line = line_number - 1

        lang_key = _LANGUAGE_ALIASES.get(language, language)
        target_types = _FUNCTION_NODE_TYPES.get(lang_key, set())
        if not target_types:
            return None

        return self._find_innermost(tree.root_node, zero_line, target_types, source_code)

    def _find_innermost(
        self,
        node: Any,
        zero_line: int,
        target_types: set[str],
        source_code: str,
    ) -> str | None:
        """DFS to find the innermost function node containing the line."""
        if node.start_point[0] > zero_line or node.end_point[0] < zero_line:
            return None

        best: str | None = None

        if node.type in target_types:
            best = self._extract_node_name(node, source_code)

        for child in node.children:
            inner = self._find_innermost(child, zero_line, target_types, source_code)
            if inner is not None:
                best = inner  # innermost wins

        return best

    # ------------------------------------------------------------------
    #  Import extraction
    # ------------------------------------------------------------------

    def extract_imports(self, tree: Any, source_code: str, language: str) -> list[str]:
        """Return a list of imported module/package names.

        Args:
            tree: Parsed tree-sitter Tree.
            source_code: Original source.
            language: Language name.

        Returns:
            List of import strings (module names or ``from … import …`` paths).
        """
        lang_key = _LANGUAGE_ALIASES.get(language, language)
        import_types = _IMPORT_NODE_TYPES.get(lang_key, set())
        if not import_types:
            return []

        imports: list[str] = []
        self._collect_imports(tree.root_node, import_types, source_code, imports)
        return imports

    def _collect_imports(
        self,
        node: Any,
        import_types: set[str],
        source_code: str,
        results: list[str],
    ) -> None:
        if node.type in import_types:
            results.append(source_code[node.start_byte : node.end_byte].strip())
        for child in node.children:
            self._collect_imports(child, import_types, source_code, results)

    # ------------------------------------------------------------------
    #  Syntax error detection (for suggestion validation)
    # ------------------------------------------------------------------

    def has_syntax_errors(self, tree: Any) -> bool:
        """Return True if the tree contains any ERROR nodes."""
        return self._find_error(tree.root_node)

    def _find_error(self, node: Any) -> bool:
        if node.type == "ERROR" or node.is_missing:
            return True
        return any(self._find_error(child) for child in node.children)

    # ------------------------------------------------------------------
    #  Scope context helper
    # ------------------------------------------------------------------

    def build_scope_context(
        self,
        source_lines: list[str],
        hunk_new_start: int,
        hunk_new_end: int,
        context_lines: int = 5,
    ) -> str:
        """Build a context window around a hunk: N lines before + hunk + N lines after.

        Args:
            source_lines: Full file split into lines (0-indexed).
            hunk_new_start: First new-file line of the hunk (1-indexed).
            hunk_new_end: Last new-file line of the hunk (1-indexed).
            context_lines: Number of surrounding lines to include.

        Returns:
            String containing the context window.
        """
        total = len(source_lines)
        # Convert to 0-indexed
        start_idx = max(0, hunk_new_start - 1 - context_lines)
        end_idx = min(total, hunk_new_end + context_lines)  # exclusive

        selected = source_lines[start_idx:end_idx]
        return "\n".join(selected)
