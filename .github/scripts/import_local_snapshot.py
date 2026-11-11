"""Import a validated local data snapshot without replacing the existing archive."""
import json
import io
from pathlib import Path
import re
import subprocess
import sys
import zipfile


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
            r"(?:posts/\d+\.html|assets/\d+/[a-f0-9]{16}\.[a-zA-Z0-9]{1,7}|recovery-chunks/\d{3}\.zip|local-recovery-(?:index|pending)\.json)", path
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
    if any(not re.fullmatch(r"\d+", value) for value in incoming_ids):
        raise ValueError("Snapshot has an invalid post ID")
    written = []
    def write(path, data):
        match = re.fullmatch(r"(?:posts/(\d+)\.html|assets/(\d+)/[a-f0-9]{16}\.[a-zA-Z0-9]{1,7})", path)
        if not match or next(value for value in match.groups() if value) not in incoming_ids:
            raise ValueError(f"Unexpected imported file: {path}")
        target = root / path
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("Imported path leaves the checkout")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(path)
    for path in files:
        if path.startswith(("assets/", "posts/")):
            write(path, git("show", f"{ref}:{path}"))
        elif path.startswith("recovery-chunks/"):
            with zipfile.ZipFile(io.BytesIO(git("show", f"{ref}:{path}"))) as archive:
                if (sum(item.file_size for item in archive.infolist()) > 25 * 1024 * 1024
                        or any(item.compress_type != zipfile.ZIP_STORED for item in archive.infolist())):
                    raise ValueError("Oversized recovery chunk")
                for item in archive.infolist():
                    write(item.filename, archive.read(item))
    if any(f"posts/{value}.html" not in written for value in incoming_ids):
        raise ValueError("Snapshot has a missing article")
    entries = sorted(existing + incoming, key=lambda entry: int(entry["id"]), reverse=True)
    queued = json.loads((root / "pending_posts.json").read_text(encoding="utf-8"))
    queue = {entry["id"]: entry for entry in queued + pending if entry["id"] not in existing_ids | incoming_ids}
    (root / "archive.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "pending_posts.json").write_text(json.dumps(list(queue.values()), ensure_ascii=False), encoding="utf-8")
    git("add", "--sparse", "--", "archive.json", "pending_posts.json", *written)
    print(f"Imported {len(incoming)} posts; total {len(entries)}; {len(queue)} still queued", flush=True)
    return len(incoming)


if __name__ == "__main__":
    ingest(Path.cwd(), sys.argv[1])
