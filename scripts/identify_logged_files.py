"""Resolve logged uploads to Zotero attachments without changing the plugin."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from mineru_like.assets import sha256
from mineru_like.contracts import Payload


def identify(storage: Path, logs: list[Path], include_success: bool = False) -> list[Payload]:
    if not storage.is_dir():
        raise ValueError(f"Zotero storage directory does not exist: {storage}")
    jobs: dict[str, Payload] = {}
    paths = set(logs)
    for log in logs:
        paths.update(log.parent.glob(log.name + ".[0-9]*"))
    for path in sorted(paths):
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                print(f"Ignoring incomplete/invalid log record: {path}:{number}", file=sys.stderr)
                continue
            if not isinstance(entry, dict) or not entry.get("job_id"):
                continue
            job = jobs.setdefault(entry["job_id"], {"job_id": entry["job_id"], "has_issue": False})
            for key in ("filename", "upload_id", "sha256"):
                if entry.get(key) is not None:
                    job[key] = entry[key]
            job["has_issue"] |= entry.get("level") in {"WARNING", "ERROR", "CRITICAL"}
    if not any(path.is_file() for path in paths):
        raise ValueError("No log files found; use --logs for a different data directory")
    index: dict[str, list[Path]] = {}
    for path in storage.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            # The unchanged plugin sends the first 12 SHA-256 hex digits of the UTF-8 basename.
            upload_name = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12] + ".pdf"
            for key in {path.name, upload_name}:
                index.setdefault(key, []).append(path)
    results = []
    hashes: dict[Path, str] = {}
    for job in jobs.values():
        if not include_success and not job["has_issue"]:
            continue
        candidates = []
        for path in sorted(index.get(job.get("filename", ""), [])):
            if job.get("sha256") and path not in hashes:
                hashes[path] = sha256(path)
            matches = hashes[path] == job["sha256"] if job.get("sha256") else None
            candidates.append(
                {
                    "path": str(path.resolve()),
                    "storage_key": path.parent.name,
                    "content_matches": matches,
                }
            )
        exact = [candidate for candidate in candidates if candidate["content_matches"]]
        results.append(
            {
                **job,
                "match": "exact_content"
                if exact
                else "filename_only"
                if len(candidates) == 1
                else "ambiguous"
                if candidates
                else "not_found",
                "candidates": exact or candidates,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--storage", type=Path, required=True, help="Zotero storage directory on this machine"
    )
    parser.add_argument(
        "--logs",
        type=Path,
        nargs="+",
        default=[Path("data/logs/server.jsonl"), Path("data/logs/issues.jsonl")],
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="include jobs without logged warnings, for silent quality errors",
    )
    args = parser.parse_args()
    try:
        for item in identify(args.storage, args.logs, args.all):
            print(json.dumps(item, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
