"""The third-party browser libraries are exactly the reviewed files, local only, with their licences."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"


def listed_checksums():
    lines = (VENDOR / "VERSIONS.txt").read_text(encoding="utf-8").splitlines()
    return [line.split(None, 1) for line in lines if line and not line.startswith("#")]


def test_every_vendored_file_matches_its_recorded_sha256():
    entries = listed_checksums()
    assert len(entries) == 4  # Two libraries, each with its LICENSE.
    for expected, relative in entries:
        data = (VENDOR / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, f"{relative} was changed"


def test_licences_and_pinned_versions_are_present():
    text = (VENDOR / "VERSIONS.txt").read_text(encoding="utf-8")
    assert "cytoscape | 3.34.4 |" in text and "cytoscape-dagre | 4.0.1 |" in text
    for folder in ("cytoscape-3.34.4", "cytoscape-dagre-4.0.1"):
        assert "MIT" in (VENDOR / folder / "LICENSE").read_text(encoding="utf-8") or \
               "Permission is hereby granted" in (VENDOR / folder / "LICENSE").read_text(encoding="utf-8")


def test_no_cdn_is_used_at_runtime():
    page = (ROOT / "templates" / "architecture.html").read_text(encoding="utf-8")
    sources = re.findall(r'<script src="([^"]+)"', page)
    assert sources and all(source.startswith("/static/") for source in sources)
    script = (ROOT / "static" / "architecture.js").read_text(encoding="utf-8")
    assert "http://" not in script and "https://" not in script
    assert ".innerHTML" not in script  # Server text is always inserted as text.
