"""Upstream source handling: pin a revision, fetch it, export TypeScript data to JSON.

Only `pin` and `fetch` touch the network. `build` reads the cache and never does.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent
LOCK_PATH = PKG_DIR / "source.lock.json"
NODE_DIR = PKG_DIR / "node"
DEFAULT_REPO = "https://github.com/smogon/pokemon-showdown.git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class SourceError(RuntimeError):
    pass


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd, cwd=None, env=None) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    except FileNotFoundError:
        raise SourceError(f"command not found: {cmd[0]}")
    if r.returncode:
        raise SourceError(f"`{' '.join(map(str, cmd))}` failed ({r.returncode}): {(r.stderr or r.stdout).strip()}")
    return r.stdout


def read_lock(path=LOCK_PATH) -> dict:
    return json.loads(Path(path).read_text())


def write_lock(lock: dict, path=LOCK_PATH):
    Path(path).write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")


def require_pinned(lock: dict) -> str:
    sha = lock.get("commit")
    if not sha or not SHA_RE.match(sha):
        raise SourceError("source.lock.json has no pinned commit. Run: python -m data_pipeline pin")
    return sha


def resolve_commit(repo: str, ref: str) -> str:
    for pattern in (f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", ref):
        out = _run(["git", "ls-remote", repo, pattern]).strip()
        if out:
            sha = out.splitlines()[0].split()[0]
            if SHA_RE.match(sha):
                return sha
    raise SourceError(f"could not resolve ref {ref!r} in {repo}")


def pin(repo: str = DEFAULT_REPO, ref: str = "master", commit: str | None = None, lock_path=LOCK_PATH) -> dict:
    if commit is not None:
        if not SHA_RE.match(commit):
            raise SourceError("--commit must be a full 40-character lowercase hex SHA")
        sha = commit
    else:
        sha = resolve_commit(repo, ref)
    lock = {"repo": repo, "ref": ref if commit is None else f"commit:{commit[:12]}", "commit": sha}
    write_lock(lock, lock_path)
    return lock


def cache_paths(root, sha: str) -> dict:
    base = Path(root) / "data" / "cache" / "showdown" / sha
    return {"base": base, "repo": base / "repo", "export": base / "export", "fetch_record": base / "fetch_record.json"}


def fetch(root, lock: dict, node_modules=None, run_npm: bool = True) -> dict:
    sha = require_pinned(lock)
    p = cache_paths(root, sha)
    p["base"].mkdir(parents=True, exist_ok=True)
    repo = p["repo"]
    if (repo / ".git").exists():
        head = _run(["git", "rev-parse", "HEAD"], cwd=repo).strip()
        if head != sha:
            raise SourceError(f"cache at {repo} is at {head}, expected {sha}; delete it and re-run fetch")
    else:
        repo.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "-q"], cwd=repo)
        _run(["git", "remote", "add", "origin", lock["repo"]], cwd=repo)
        _run(["git", "fetch", "-q", "--depth", "1", "origin", sha], cwd=repo)
        _run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=repo)
        head = _run(["git", "rev-parse", "HEAD"], cwd=repo).strip()
        if head != sha:
            raise SourceError(f"fetched {head}, expected pinned {sha}")
    licenses = {q.name: sha256_file(q) for q in sorted(repo.iterdir()) if q.is_file() and q.name.upper().startswith(("LICENSE", "COPYING"))}
    record = {"repo": lock["repo"], "commit": sha, "license_files_sha256": licenses,
              "fetched_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "git_version": _run(["git", "--version"]).strip()}
    p["fetch_record"].write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    export(root, sha, node_modules=node_modules, run_npm=run_npm)
    return record


def export(root, sha: str, node_modules=None, run_npm: bool = True) -> Path:
    p = cache_paths(root, sha)
    node = shutil.which("node")
    if not node:
        raise SourceError("node not found on PATH (install Node.js, e.g. `brew install node`)")
    env = os.environ.copy()
    if node_modules:
        env["NODE_PATH"] = str(node_modules)
    elif not (NODE_DIR / "node_modules" / "sucrase").exists():
        if not run_npm:
            raise SourceError("sucrase not installed; run `npm install` in data_pipeline/node")
        _run(["npm", "install", "--no-audit", "--no-fund"], cwd=NODE_DIR)
    if p["export"].exists():
        shutil.rmtree(p["export"])
    _run([node, str(NODE_DIR / "export_showdown.cjs"), str(p["repo"]), str(p["export"])], env=env)
    return p["export"]
