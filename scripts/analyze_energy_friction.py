#!/usr/bin/env python3
"""Read-only, privacy-preserving first-pass friction scan for the chat archive.

This emits counts and short generalized source references. It never writes to
the archive and never includes message bodies in the report.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

SIGNALS = {
    "obviousness_marker": re.compile(r"\b(obviously|clearly|surely|similarly|common sense)\b|\betc\.?", re.I),
    "intelligence_insult": re.compile(r"\b(dumb|stupid|idiot|ridiculous|incompetent)\b", re.I),
    "continuation_marker": re.compile(r"\b(keep going|continue|hands off|come back later|follow through)\b", re.I),
    "premature_stop": re.compile(r"(why (did|would|could|can't|can.t) you|just (do|try|use)|figure it out)", re.I),
}

PLATFORMS = ("chatgpt", "claude", "claude_code", "codex", "gemini", "copilot", "cursor", "agent")


def iter_texts(root: Path):
    archive = root / "ai_conversations.zip"
    if archive.exists():
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if name.startswith("ai-conversations/") and name.endswith((".md", ".json", ".jsonl")):
                    yield name, bundle.read(name).decode("utf-8", "replace")
    for path in (root / "ai-conversations").glob("**/*"):
        if path.is_file() and path.suffix in {".md", ".json", ".jsonl"}:
            yield str(path.relative_to(root)), path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Count energy-friction signals without exporting message text.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    counts = Counter()
    files = Counter()
    for name, text in iter_texts(args.root):
        for label, pattern in SIGNALS.items():
            hits = len(pattern.findall(text))
            if hits:
                counts[label] += hits
                files[label] += 1
    result = {
        "schema_version": "protecting-your-energy/friction-scan/v1",
        "root": "local archive (message bodies omitted)",
        "activation": "ask_user",
        "counts": dict(sorted(counts.items())),
        "files_with_signal": dict(sorted(files.items())),
        "platform_scope": PLATFORMS,
        "interpretation": "Signals identify episodes for human review; they do not create or activate user policies.",
    }
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else "\n".join(f"{k}: {v}" for k, v in sorted(result["counts"].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
