"""Create ../parijat-garg-pe-assignment.zip without venv/caches.   python make_zip.py"""
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
out = root.parent / "parijat-garg-pe-assignment.zip"
skip = {".venv", "venv", "__pycache__", ".pytest_cache", ".git"}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in root.rglob("*"):
        if f.is_file() and not (set(f.relative_to(root).parts) & skip) and f.suffix not in (".pyc", ".db", ".tmp"):   # hx50.db is rebuilt by `python -m hx50.build`
            z.write(f, Path(root.name) / f.relative_to(root))
print(out, round(out.stat().st_size / 1e6, 2), "MB")
