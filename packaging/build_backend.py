"""Bundle the backend into one executable for the VS Code extension: vscode-extension/bin/reposense-backend[.exe].

Run from the repository root with the dev environment active:  python packaging/build_backend.py
The executable is for the platform it is built on; CI builds one per platform (.github/workflows/release.yml).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "vscode-extension" / "bin"
NAME = "reposense-backend"


def main() -> None:
    work = ROOT / "build" / "pyinstaller"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "backend_entry.py"),
        "--onefile", "--name", NAME, "--noconfirm", "--clean",
        "--distpath", str(OUT), "--workpath", str(work), "--specpath", str(work),
        "--paths", str(ROOT / "src"),
        # the server never uses the CLI's libraries
        "--exclude-module", "rich", "--exclude-module", "click", "--exclude-module", "tkinter",
        # optional extras of the openai SDK (embeddings helpers); not used
        "--exclude-module", "numpy", "--exclude-module", "pandas",
    ], check=True)
    shutil.rmtree(work, ignore_errors=True)
    exe = OUT / (NAME + (".exe" if sys.platform == "win32" else ""))
    print(f"built {exe} ({exe.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
