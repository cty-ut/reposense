"""Code chunking via tree-sitter AST, with fallback to line-based splitting."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser, Node

logger = logging.getLogger(__name__)

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

_EXT_TO_LANGUAGE: dict[str, Language] = {
    ".py": PY_LANGUAGE,
    ".js": JS_LANGUAGE,
    ".jsx": JS_LANGUAGE,
    ".ts": TS_LANGUAGE,
    ".tsx": TSX_LANGUAGE,
    ".mjs": JS_LANGUAGE,
    ".cjs": JS_LANGUAGE,
}

_TOP_LEVEL_TYPES = {
    "function_definition",       # Python
    "class_definition",          # Python
    "decorated_definition",      # Python @decorator
    "function_declaration",      # JS/TS
    "class_declaration",         # JS/TS
    "export_statement",          # JS/TS export ...
    "lexical_declaration",       # JS/TS const/let
    "variable_declaration",      # JS/TS var
    "interface_declaration",     # TS
    "type_alias_declaration",    # TS
    "enum_declaration",          # TS
}

MAX_CHUNK_LINES = 150
MIN_CHUNK_LINES = 2
FALLBACK_CHUNK_LINES = 60


@dataclass
class CodeChunk:
    """A semantically meaningful piece of code."""
    content: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str   # function/class name, or "module-level"
    symbol_type: str   # "function" / "class" / "module" / "block"
    language: str


def _get_symbol_name(node: Node, source_bytes: bytes) -> str:
    """Extract the name of a function/class from its AST node."""
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        if child.type == "name":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    # For decorated_definition / export_statement, look inside the inner definition
    if node.type in ("decorated_definition", "export_statement"):
        for child in node.children:
            if child.type in ("function_definition", "class_definition",
                              "function_declaration", "class_declaration"):
                return _get_symbol_name(child, source_bytes)
    return "anonymous"


def _get_symbol_type(node: Node) -> str:
    t = node.type
    if "class" in t:
        return "class"
    if "function" in t:
        return "function"
    if t in ("decorated_definition", "export_statement"):
        for child in node.children:
            if "class" in child.type:
                return "class"
            if "function" in child.type:
                return "function"
    return "block"


def _split_large_class(node: Node, source_bytes: bytes, file_path: str, lang_name: str) -> list[CodeChunk]:
    """If a class is too large, split it into per-method chunks."""
    method_types = {"function_definition", "function_declaration", "method_definition"}
    chunks: list[CodeChunk] = []
    body = None
    for child in node.children:
        if child.type in ("block", "class_body", "statement_block"):
            body = child
            break
    if body is None:
        body = node

    # Class header (up to the first method)
    header_end = body.start_point[0]
    non_method_lines: list[str] = []
    current_methods: list[Node] = []

    for child in body.children:
        if child.type in method_types or (child.type == "decorated_definition"):
            current_methods.append(child)
        else:
            text = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace").strip()
            if text:
                non_method_lines.append(text)

    class_name = _get_symbol_name(node, source_bytes)

    # Module-level part of the class (attributes, etc.)
    if non_method_lines:
        header_text = source_bytes[node.start_byte:body.start_byte].decode("utf-8", errors="replace")
        attr_text = "\n".join(non_method_lines)
        combined = header_text.rstrip() + "\n" + attr_text
        chunks.append(CodeChunk(
            content=combined,
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=body.start_point[0] + 1 + len(non_method_lines),
            symbol_name=f"{class_name} (attributes)",
            symbol_type="class",
            language=lang_name,
        ))

    for method_node in current_methods:
        text = source_bytes[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="replace")
        method_name = _get_symbol_name(method_node, source_bytes)
        chunks.append(CodeChunk(
            content=text,
            file_path=file_path,
            start_line=method_node.start_point[0] + 1,
            end_line=method_node.end_point[0] + 1,
            symbol_name=f"{class_name}.{method_name}",
            symbol_type="function",
            language=lang_name,
        ))

    return chunks if chunks else [CodeChunk(
        content=source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace"),
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        symbol_name=class_name,
        symbol_type="class",
        language=lang_name,
    )]


def chunk_with_treesitter(source: str, file_path: str) -> list[CodeChunk]:
    """Parse source code with tree-sitter and split into semantic chunks."""
    ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    lang = _EXT_TO_LANGUAGE.get(ext)
    if lang is None:
        return []

    lang_name = ext.lstrip(".")
    source_bytes = source.encode("utf-8")

    parser = Parser(lang)
    tree = parser.parse(source_bytes)

    chunks: list[CodeChunk] = []
    module_segments: list[tuple[int, int]] = []  # (start_byte, end_byte)
    covered_ranges: list[tuple[int, int]] = []

    root = tree.root_node
    for child in root.children:
        if child.type in _TOP_LEVEL_TYPES:
            line_count = child.end_point[0] - child.start_point[0] + 1
            symbol_type = _get_symbol_type(child)

            if symbol_type == "class" and line_count > MAX_CHUNK_LINES:
                chunks.extend(_split_large_class(child, source_bytes, file_path, lang_name))
            else:
                text = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                if text.strip():
                    chunks.append(CodeChunk(
                        content=text,
                        file_path=file_path,
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        symbol_name=_get_symbol_name(child, source_bytes),
                        symbol_type=symbol_type,
                        language=lang_name,
                    ))
            covered_ranges.append((child.start_byte, child.end_byte))

    # Collect module-level code (imports, constants, etc.) not inside functions/classes
    covered_ranges.sort()
    prev_end = 0
    for start, end in covered_ranges:
        if start > prev_end:
            segment = source_bytes[prev_end:start].decode("utf-8", errors="replace").strip()
            if segment:
                module_segments.append((prev_end, start))
        prev_end = max(prev_end, end)
    # Trailing module-level code
    if prev_end < len(source_bytes):
        segment = source_bytes[prev_end:].decode("utf-8", errors="replace").strip()
        if segment:
            module_segments.append((prev_end, len(source_bytes)))

    if module_segments:
        combined = "\n".join(
            source_bytes[s:e].decode("utf-8", errors="replace").strip()
            for s, e in module_segments
        )
        if combined.strip():
            lines = source.split("\n")
            chunks.insert(0, CodeChunk(
                content=combined,
                file_path=file_path,
                start_line=1,
                end_line=len(lines),
                symbol_name="module-level",
                symbol_type="module",
                language=lang_name,
            ))

    return [c for c in chunks if c.content.strip() and (c.end_line - c.start_line + 1) >= MIN_CHUNK_LINES]


def chunk_with_lines(source: str, file_path: str, chunk_size: int = FALLBACK_CHUNK_LINES) -> list[CodeChunk]:
    """Fallback: split source into chunks by blank-line boundaries or fixed size."""
    ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "txt"
    lines = source.split("\n")

    if len(lines) <= chunk_size:
        return [CodeChunk(
            content=source,
            file_path=file_path,
            start_line=1,
            end_line=len(lines),
            symbol_name="file",
            symbol_type="module",
            language=ext.lstrip("."),
        )]

    chunks: list[CodeChunk] = []
    current_start = 0

    while current_start < len(lines):
        end = min(current_start + chunk_size, len(lines))

        # Try to break at a blank line near the boundary
        best_break = end
        for i in range(end, max(current_start + chunk_size // 2, current_start), -1):
            if i < len(lines) and lines[i].strip() == "":
                best_break = i + 1
                break

        chunk_lines = lines[current_start:best_break]
        content = "\n".join(chunk_lines)
        if content.strip():
            chunks.append(CodeChunk(
                content=content,
                file_path=file_path,
                start_line=current_start + 1,
                end_line=best_break,
                symbol_name=f"lines {current_start + 1}-{best_break}",
                symbol_type="block",
                language=ext.lstrip("."),
            ))
        current_start = best_break

    return chunks


def chunk_code(source: str, file_path: str) -> list[CodeChunk]:
    """Smart chunking: try tree-sitter first, fall back to line-based."""
    ts_chunks = chunk_with_treesitter(source, file_path)
    if ts_chunks:
        return ts_chunks
    return chunk_with_lines(source, file_path)
