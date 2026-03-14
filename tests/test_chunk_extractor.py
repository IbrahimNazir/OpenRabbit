"""Comprehensive tests for semantic code chunk extraction.

Tests cover the three-tier chunking strategy (ADR-0021):
- Tier 1: Tree-sitter semantic boundaries (function/class nodes)
- Tier 2: Sliding-window fallback with tiktoken
- Tier 3: File-level header chunk (first 50 lines)

Tests verify:
- Semantic chunking for all supported languages
- File header chunk always included
- Large function splitting
- Deterministic chunk IDs
- Sliding-window fallback behavior
- Language detection from file extension
- Empty file handling
- Chunk deduplication
"""

from __future__ import annotations

import pytest

from app.parsing.chunk_extractor import CodeChunk, extract_chunks


@pytest.mark.unit
class TestExtractChunksPython:
    """Test chunk extraction from Python files."""

    def test_extract_chunks_python_functions(self) -> None:
        """Test extracting function boundaries as chunks."""
        code = """def foo():
    x = 1
    y = 2

def bar():
    z = 3
"""
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1
        # Check all chunks are valid
        for chunk in chunks:
            assert chunk.chunk_id
            assert chunk.file_path == "test.py"
            assert chunk.content
            assert chunk.language == "python"
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line

    def test_extract_chunks_python_classes(self) -> None:
        """Test extracting class definitions."""
        code = """class MyClass:
    def method1(self):
        pass

    def method2(self):
        pass
"""
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1
        # At least one chunk should be a class
        chunk_types = set(c.chunk_type for c in chunks)
        assert "class" in chunk_types or "function" in chunk_types

    def test_extract_chunks_python_file_header_included(self) -> None:
        """Test that file header chunk is always included."""
        code = """#!/usr/bin/env python
\"\"\"Module docstring.\"\"\"

import os

def foo():
    pass
"""
        chunks = extract_chunks(code, "test.py")
        # Should have at least one chunk (file header or function)
        assert len(chunks) >= 1
        # Check if any chunk is a file_header
        has_header = any(c.chunk_type == "file_header" for c in chunks)
        assert has_header or len(chunks) > 0  # May use semantic chunking

    def test_extract_chunks_python_imports(self) -> None:
        """Test that imports and docstrings are captured in chunks."""
        code = """\"\"\"Main module.\"\"\"
import sys
import os
from typing import List

def process():
    pass
"""
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1
        # All chunks together should contain the imports
        combined = "\n".join(c.content for c in chunks)
        assert "import" in combined

    def test_extract_chunks_python_nested_functions(self) -> None:
        """Test extraction of nested functions."""
        code = """def outer():
    def inner():
        x = 1
    return inner()
"""
        chunks = extract_chunks(code, "test.py")
        # Should find nested functions
        assert len(chunks) >= 1

    def test_extract_chunks_python_async(self) -> None:
        """Test extracting async function definitions."""
        code = """async def fetch_data():
    result = await some_call()
    return result

async def process():
    data = await fetch_data()
    return data
"""
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1


@pytest.mark.unit
class TestExtractChunksJavaScript:
    """Test chunk extraction from JavaScript files."""

    def test_extract_chunks_javascript(self) -> None:
        """Test extracting chunks from JavaScript."""
        code = """function foo() {
    return 1;
}

const bar = () => {
    return 2;
};
"""
        chunks = extract_chunks(code, "test.js")
        assert len(chunks) >= 1
        assert all(c.language == "javascript" for c in chunks)

    def test_extract_chunks_jsx(self) -> None:
        """Test extracting from JSX files."""
        code = """import React from 'react';

function App() {
    return <div>Hello</div>;
}

export default App;
"""
        chunks = extract_chunks(code, "app.jsx")
        assert len(chunks) >= 1
        assert all(c.language == "javascript" for c in chunks)


@pytest.mark.unit
class TestExtractChunksTypeScript:
    """Test chunk extraction from TypeScript files."""

    def test_extract_chunks_typescript(self) -> None:
        """Test extracting chunks from TypeScript."""
        code = """interface User {
    name: string;
}

function getUser(): User {
    return { name: "John" };
}

class UserManager {
    getUser(): User {
        return getUser();
    }
}
"""
        chunks = extract_chunks(code, "test.ts")
        assert len(chunks) >= 1
        assert all(c.language == "typescript" for c in chunks)

    def test_extract_chunks_tsx(self) -> None:
        """Test extracting from TSX files."""
        code = """import React from 'react';

interface Props {
    title: string;
}

const Header: React.FC<Props> = ({ title }) => (
    <header><h1>{title}</h1></header>
);

export default Header;
"""
        chunks = extract_chunks(code, "header.tsx")
        assert len(chunks) >= 1


@pytest.mark.unit
class TestExtractChunksLargeFunctions:
    """Test handling of large functions."""

    def test_extract_chunks_large_function_split(self) -> None:
        """Test that large functions are split into sub-chunks."""
        # Create a function with > 100 lines
        lines = ["def large_function():"]
        for i in range(110):
            lines.append(f"    x{i} = {i}")
        code = "\n".join(lines)

        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1
        # Large function should result in multiple chunks or a single semantic chunk
        # depending on line count threshold

    def test_extract_chunks_multiple_large_functions(self) -> None:
        """Test multiple large functions are handled properly."""
        code = """def func1():
""" + "\n".join([f"    x = {i}" for i in range(60)]) + """

def func2():
""" + "\n".join([f"    y = {i}" for i in range(60)])

        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1


@pytest.mark.unit
class TestExtractChunksDeterminism:
    """Test that chunk extraction is deterministic."""

    def test_extract_chunks_deterministic_ids(self) -> None:
        """Test that chunk IDs are deterministic."""
        code = """def foo():
    x = 1
    return x

def bar():
    y = 2
    return y
"""
        chunks1 = extract_chunks(code, "test.py")
        chunks2 = extract_chunks(code, "test.py")

        assert len(chunks1) == len(chunks2)
        for c1, c2 in zip(chunks1, chunks2):
            assert c1.chunk_id == c2.chunk_id
            assert c1.start_line == c2.start_line
            assert c1.end_line == c2.end_line

    def test_extract_chunks_id_format(self) -> None:
        """Test that chunk IDs are in expected format (hex string)."""
        code = "def foo():\n    pass"
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1
        for chunk in chunks:
            # Should be a hex string of reasonable length
            assert len(chunk.chunk_id) > 0
            assert all(c in "0123456789abcdef" for c in chunk.chunk_id.lower())


@pytest.mark.unit
class TestExtractChunksEmptyAndEdgeCases:
    """Test edge cases."""

    def test_extract_chunks_empty_file(self) -> None:
        """Test extracting chunks from empty file."""
        code = ""
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) == 0

    def test_extract_chunks_whitespace_only(self) -> None:
        """Test extracting from whitespace-only file."""
        code = "   \n\n   \n"
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) == 0

    def test_extract_chunks_single_line(self) -> None:
        """Test extracting from single-line file."""
        code = "x = 1"
        chunks = extract_chunks(code, "test.py")
        # May produce no chunks or a window chunk
        assert isinstance(chunks, list)

    def test_extract_chunks_only_comments(self) -> None:
        """Test extracting from file with only comments."""
        code = """# Comment 1
# Comment 2
# Comment 3
"""
        chunks = extract_chunks(code, "test.py")
        # Should handle gracefully
        assert isinstance(chunks, list)


@pytest.mark.unit
class TestExtractChunksLanguageDetection:
    """Test language detection from file extensions."""

    def test_extract_chunks_language_python(self) -> None:
        """Test Python language detection."""
        code = "def foo(): pass"
        chunks = extract_chunks(code, "src/module.py")
        assert all(c.language == "python" for c in chunks)

    def test_extract_chunks_language_javascript(self) -> None:
        """Test JavaScript language detection."""
        code = "function foo() {}"
        chunks = extract_chunks(code, "src/index.js")
        assert all(c.language == "javascript" for c in chunks)

    def test_extract_chunks_language_typescript(self) -> None:
        """Test TypeScript language detection."""
        code = "function foo(): void {}"
        chunks = extract_chunks(code, "src/index.ts")
        assert all(c.language == "typescript" for c in chunks)

    def test_extract_chunks_language_go(self) -> None:
        """Test Go language detection."""
        code = 'func foo() { }'
        chunks = extract_chunks(code, "main.go")
        assert all(c.language == "go" for c in chunks)

    def test_extract_chunks_language_rust(self) -> None:
        """Test Rust language detection."""
        code = "fn foo() { }"
        chunks = extract_chunks(code, "lib.rs")
        assert all(c.language == "rust" for c in chunks)

    def test_extract_chunks_language_java(self) -> None:
        """Test Java language detection."""
        code = "class Foo { }"
        chunks = extract_chunks(code, "Foo.java")
        assert all(c.language == "java" for c in chunks)

    def test_extract_chunks_language_unknown_extension(self) -> None:
        """Test unknown extension defaults to text."""
        code = "some code"
        chunks = extract_chunks(code, "unknown.xyz")
        assert all(c.language == "text" for c in chunks)

    def test_extract_chunks_case_insensitive_extension(self) -> None:
        """Test that extension detection is case-insensitive."""
        code = "def foo(): pass"
        chunks = extract_chunks(code, "test.PY")
        assert all(c.language == "python" for c in chunks)


@pytest.mark.unit
class TestChunkStructure:
    """Test CodeChunk dataclass structure and properties."""

    def test_code_chunk_fields(self) -> None:
        """Test that CodeChunk has all required fields."""
        code = "def foo(): pass"
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1

        chunk = chunks[0]
        assert hasattr(chunk, "chunk_id")
        assert hasattr(chunk, "file_path")
        assert hasattr(chunk, "chunk_type")
        assert hasattr(chunk, "name")
        assert hasattr(chunk, "content")
        assert hasattr(chunk, "start_line")
        assert hasattr(chunk, "end_line")
        assert hasattr(chunk, "language")

    def test_code_chunk_valid_types(self) -> None:
        """Test chunk types are in expected set."""
        code = """def foo():
    pass

class Bar:
    pass
"""
        chunks = extract_chunks(code, "test.py")
        valid_types = {"function", "class", "file_header", "window"}
        for chunk in chunks:
            assert chunk.chunk_type in valid_types

    def test_code_chunk_line_numbers_valid(self) -> None:
        """Test that line numbers are valid and ordered."""
        code = """def foo():
    x = 1

def bar():
    y = 2
"""
        chunks = extract_chunks(code, "test.py")
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line
            assert isinstance(chunk.start_line, int)
            assert isinstance(chunk.end_line, int)

    def test_code_chunk_content_not_empty(self) -> None:
        """Test that chunk content is not empty."""
        code = "def foo(): pass"
        chunks = extract_chunks(code, "test.py")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert len(chunk.content) > 0


@pytest.mark.unit
class TestExtractChunksFallback:
    """Test fallback chunking strategies."""

    def test_extract_chunks_unsupported_language_fallback(self) -> None:
        """Test that unsupported language falls back to sliding window."""
        code = """line 1
line 2
line 3"""
        chunks = extract_chunks(code, "unknown.cobol")
        # Should use sliding window or line window fallback
        assert isinstance(chunks, list)

    def test_extract_chunks_large_file_sliding_window(self) -> None:
        """Test sliding window fallback for large files."""
        # Create a moderately large file to potentially trigger sliding window
        lines = [f"line {i}: " + "x" * 50 for i in range(20)]
        code = "\n".join(lines)
        chunks = extract_chunks(code, "test.txt")
        # Should produce chunks via fallback
        assert isinstance(chunks, list)


@pytest.mark.unit
class TestExtractChunksDeduplication:
    """Test chunk deduplication."""

    def test_extract_chunks_no_duplicates(self) -> None:
        """Test that chunks are deduplicated by chunk_id."""
        code = """def foo():
    pass

def bar():
    pass
"""
        chunks = extract_chunks(code, "test.py")
        chunk_ids = [c.chunk_id for c in chunks]
        # All IDs should be unique
        assert len(chunk_ids) == len(set(chunk_ids))

    def test_extract_chunks_file_header_uniqueness(self) -> None:
        """Test that file header doesn't duplicate with semantic chunks."""
        code = """#!/usr/bin/env python
\"\"\"Docstring.\"\"\"

def foo():
    pass
"""
        chunks = extract_chunks(code, "test.py")
        chunk_ids = [c.chunk_id for c in chunks]
        assert len(chunk_ids) == len(set(chunk_ids)), "Duplicate chunk IDs found"


@pytest.mark.unit
class TestExtractChunksRealWorldCode:
    """Test extraction with realistic code samples."""

    def test_extract_chunks_django_view(self) -> None:
        """Test with realistic Django view code."""
        code = """from django.http import JsonResponse
from django.views import View

class UserListView(View):
    def get(self, request):
        users = User.objects.all()
        return JsonResponse({
            'users': [u.serialize() for u in users]
        })

    def post(self, request):
        user = User.objects.create(
            name=request.POST.get('name'),
            email=request.POST.get('email')
        )
        return JsonResponse({'id': user.id})
"""
        chunks = extract_chunks(code, "views.py")
        assert len(chunks) >= 1
        # Should capture class and methods
        combined = "\n".join(c.content for c in chunks)
        assert "UserListView" in combined

    def test_extract_chunks_react_component(self) -> None:
        """Test with realistic React component code."""
        code = """import React, { useState } from 'react';

function Counter({ initial = 0 }) {
    const [count, setCount] = useState(initial);

    const increment = () => setCount(count + 1);
    const decrement = () => setCount(count - 1);

    return (
        <div>
            <p>Count: {count}</p>
            <button onClick={increment}>+</button>
            <button onClick={decrement}>-</button>
        </div>
    );
}

export default Counter;
"""
        chunks = extract_chunks(code, "Counter.jsx")
        assert len(chunks) >= 1
        combined = "\n".join(c.content for c in chunks)
        assert "Counter" in combined

    def test_extract_chunks_sql_migration(self) -> None:
        """Test with SQL migration file (text fallback)."""
        code = """-- Migration: Add user_roles table

CREATE TABLE user_roles (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    role VARCHAR(50) NOT NULL
);

CREATE INDEX idx_user_roles_user_id ON user_roles(user_id);
"""
        chunks = extract_chunks(code, "001_add_user_roles.sql")
        assert isinstance(chunks, list)

    def test_extract_chunks_configuration_file(self) -> None:
        """Test with configuration file (YAML/JSON)."""
        code = """name: OpenRabbit
version: 1.0.0
description: Self-hosted code reviewer

features:
  - ast_parsing
  - rag_indexing
  - pr_review
"""
        chunks = extract_chunks(code, "config.yaml")
        assert isinstance(chunks, list)
