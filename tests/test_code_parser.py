"""Tests for tree-sitter code chunking and line-based fallback."""

from __future__ import annotations

from reposense.tools.code_parser import chunk_code, chunk_with_treesitter, chunk_with_lines

PYTHON_SAMPLE = '''\
import os
import sys

MAX_SIZE = 1024


def hello(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"


def goodbye(name: str) -> str:
    return f"Goodbye, {name}!"


class Calculator:
    """A simple calculator."""

    def __init__(self):
        self.result = 0

    def add(self, x: int) -> int:
        self.result += x
        return self.result

    def subtract(self, x: int) -> int:
        self.result -= x
        return self.result

    def reset(self):
        self.result = 0
'''

TS_SAMPLE = '''\
import { useState } from 'react';

interface User {
  id: number;
  name: string;
}

export function greet(user: User): string {
  return `Hello, ${user.name}!`;
}

export class UserService {
  private users: User[] = [];

  addUser(user: User): void {
    this.users.push(user);
  }

  getUser(id: number): User | undefined {
    return this.users.find(u => u.id === id);
  }
}

const DEFAULT_USER: User = { id: 0, name: 'Anonymous' };
'''

GO_SAMPLE = '''\
package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
'''


class TestTreeSitterPython:

    def test_splits_functions(self):
        chunks = chunk_with_treesitter(PYTHON_SAMPLE, "example.py")
        names = [c.symbol_name for c in chunks]
        assert "hello" in names
        assert "goodbye" in names

    def test_splits_classes(self):
        chunks = chunk_with_treesitter(PYTHON_SAMPLE, "example.py")
        class_chunks = [c for c in chunks if "Calculator" in c.symbol_name]
        assert len(class_chunks) >= 1

    def test_captures_module_level(self):
        chunks = chunk_with_treesitter(PYTHON_SAMPLE, "example.py")
        module_chunks = [c for c in chunks if c.symbol_type == "module"]
        assert len(module_chunks) >= 1
        module_text = module_chunks[0].content
        assert "import os" in module_text

    def test_metadata_correctness(self):
        chunks = chunk_with_treesitter(PYTHON_SAMPLE, "src/utils.py")
        for chunk in chunks:
            assert chunk.file_path == "src/utils.py"
            assert chunk.language == "py"
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line


class TestTreeSitterTypeScript:

    def test_splits_functions_and_classes(self):
        chunks = chunk_with_treesitter(TS_SAMPLE, "service.ts")
        names = [c.symbol_name for c in chunks]
        assert any("greet" in n for n in names)
        assert any("UserService" in n for n in names)

    def test_language_tag(self):
        chunks = chunk_with_treesitter(TS_SAMPLE, "service.ts")
        assert all(c.language == "ts" for c in chunks)

    def test_tsx_support(self):
        chunks = chunk_with_treesitter(TS_SAMPLE, "component.tsx")
        assert len(chunks) >= 1
        assert all(c.language == "tsx" for c in chunks)


class TestFallback:

    def test_unsupported_language_returns_empty_treesitter(self):
        chunks = chunk_with_treesitter(GO_SAMPLE, "main.go")
        assert chunks == []

    def test_line_fallback_works(self):
        chunks = chunk_with_lines(GO_SAMPLE, "main.go")
        assert len(chunks) >= 1
        assert chunks[0].language == "go"

    def test_chunk_code_auto_selects(self):
        py_chunks = chunk_code(PYTHON_SAMPLE, "app.py")
        assert len(py_chunks) >= 3  # module + hello + goodbye + Calculator

        go_chunks = chunk_code(GO_SAMPLE, "main.go")
        assert len(go_chunks) >= 1  # line-based fallback

    def test_line_chunking_large_file(self):
        large = "\n".join(f"line {i}" for i in range(200))
        chunks = chunk_with_lines(large, "big.go", chunk_size=60)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert chunk.end_line - chunk.start_line + 1 <= 70  # some leeway for boundary
