"""Single entry point for the portable Windows build.

Importing both modules here is intentional: it lets PyInstaller discover all
third-party imports.  Calling their main() functions avoids runpy/__main__
failures inside a frozen executable.
"""
import sys
import os
import time
import shutil
import tempfile
import zipfile
from pathlib import Path
import app
import launcher


def apply_update(archive_path, install_dir):
    """Replace the portable build after the original process has exited."""
    archive = Path(archive_path)
    target = Path(install_dir).resolve()
    time.sleep(2.0)
    with tempfile.TemporaryDirectory(prefix="jarvis-update-") as staging:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
        root = Path(staging)
        nested = root / "Jarvis"
        if nested.is_dir():
            root = nested
        for item in root.iterdir():
            destination = target / item.name
            if item.name.lower() in {"jarvis.db", ".env"}:
                continue
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination)
    try:
        archive.unlink()
    except OSError:
        pass
    executable = target / "Jarvis.exe"
    if executable.exists():
        os.startfile(str(executable))


if "--apply-update" in sys.argv:
    try:
        index = sys.argv.index("--apply-update")
        apply_update(sys.argv[index + 1], sys.argv[index + 2])
    except (IndexError, OSError, zipfile.BadZipFile, ValueError):
        pass
    raise SystemExit(0)

if "--headless" in sys.argv:
    app.main()
else:
    launcher.main()