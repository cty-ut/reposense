"""Entry point of the bundled backend: only the JSON-RPC server the VS Code extension talks to."""

from reposense.server import main

if __name__ == "__main__":
    main()
