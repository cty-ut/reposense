"""Static import graph (no git, no model)."""

from reposense.analysis.dependencies import PythonModules, js_imports, package_exports, python_imports

PY_FILES = {
    "src/pkg/__init__.py": "from .core import Command\nfrom . import utils\n",
    "src/pkg/core.py": "import os\nfrom .utils import helper\nfrom pkg.parser import parse\n",
    "src/pkg/utils.py": "def helper(): ...\n",
    "src/pkg/parser.py": "from ..pkg import utils\n",
    "tests/conftest.py": "",
    "tests/test_core.py": "import pkg\nimport conftest\nfrom pkg import Command\n\ndef test(): pkg.Command()\n",
}


def imports_of(path):
    modules = PythonModules(list(PY_FILES))
    exports = package_exports(PY_FILES, modules)
    return sorted(set(python_imports(path, PY_FILES[path], modules, exports)))


def test_python_absolute_relative_and_submodule_imports():
    assert imports_of("src/pkg/core.py") == ["src/pkg/parser.py", "src/pkg/utils.py"]  # os is ignored
    assert imports_of("src/pkg/__init__.py") == ["src/pkg/core.py", "src/pkg/utils.py"]


def test_python_follows_package_reexports_and_loose_modules():
    # `from pkg import Command` and `pkg.Command()` both point at core.py, where Command is defined
    assert imports_of("tests/test_core.py") == ["src/pkg/__init__.py", "src/pkg/core.py", "tests/conftest.py"]


def test_relative_import_beyond_top_package_is_ignored():
    assert imports_of("src/pkg/parser.py") == []


def test_js_relative_imports_resolve_extensions_and_index():
    files = {"web/app.tsx", "web/api/index.ts", "web/util.ts", "web/style.css"}
    source = """
        import React from 'react';
        import { get } from './api';
        export * from "./util.js";
        const lazy = import('./missing');
        import './style.css';
    """
    assert sorted(js_imports("web/app.tsx", source, files)) == ["web/api/index.ts", "web/style.css", "web/util.ts"]
