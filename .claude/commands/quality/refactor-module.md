Restructure the module: $ARGUMENTS

Read all files; identify: files >200 lines, poor separation of concerns, circular imports.
Split large files into focused submodules; update all imports; keep public API unchanged (update __init__.py).
