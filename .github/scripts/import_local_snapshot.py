"""Import a validated local data snapshot without replacing the existing archive."""
import json
from pathlib import Path
import re
import subprocess
import sys


def ingest(root, ref):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    files = []
    for row in git("ls-tree", "-rz", ref).split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        path = name.decode("utf-8")
        if metadata.split()[:2] != [b"100644", b"blob"] or not re.fullmatch(
            r"(?:posts/\d+\.html|assets/\d+/[a-f0-9]{16}\.[a-zA-Z0-9]{1,7}|local-recovery-(?:index|pending)\.json)", path
        ):
            raise ValueError(f"Unexpected snapshot entry: {path}")
        files.append(path)
    incoming = json.loads(git("show", f"{ref}:local-recovery-index.json"))
    pending = json.loads(git("show", f"{ref}:local-recovery-pending.json"))
    existing = json.loads((root / "archive.json").read_text(encoding="utf-8"))
    existing_ids = {entry["id"] for entry in existing}
    incoming_ids = {entry["id"] for entry in incoming}
    if not incoming or len(incoming_ids) != len(incoming) or incoming_ids & existing_ids:
        raise ValueError("Snapshot is empty, duplicated, or overlaps already archived posts")
    if any(not re.fullmatch(r"\d+", value) or f"posts/{value}.html" not in files for value in incoming_ids):
        raise ValueError("Snapshot has an invalid post ID or missing article")
    git("checkout", "--ignore-skip-worktree-bits", ref, "--", *[p for p in files if p.startswith(("assets/", "posts/"))])
    entries = sorted(existing + incoming, key=lambda entry: int(entry["id"]), reverse=True)
    queued = json.loads((root / "pending_posts.json").read_text(encoding="utf-8"))
    queue = {entry["id"]: entry for entry in queued + pending if entry["id"] not in existing_ids | incoming_ids}
    (root / "archive.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "pending_posts.json").write_text(json.dumps(list(queue.values()), ensure_ascii=False), encoding="utf-8")
    git("add", "--sparse", "archive.json", "pending_posts.json")
    print(f"Imported {len(incoming)} posts; total {len(entries)}; {len(queue)} still queued", flush=True)
    return len(incoming)


if __name__ == "__main__":
    ingest(Path.cwd(), sys.argv[1])
