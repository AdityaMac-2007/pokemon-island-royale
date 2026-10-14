import hashlib, json, shutil, sys, tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
ENGINE_DIR = ROOT / "Claude engine"
FIXTURE_EXPORT = Path(__file__).resolve().parent / "fixtures" / "export"
FIXTURE_TS = Path(__file__).resolve().parent / "fixtures" / "showdown_ts"
FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"
for p in (str(ENGINE_DIR), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def make_cache(root: Path, sha: str = FAKE_SHA) -> Path:
    """Lay out a cache dir exactly as `fetch` would, from the golden JSON fixtures (no network, no node)."""
    export = root / "data" / "cache" / "showdown" / sha / "export"
    export.mkdir(parents=True)
    files = {}
    for src in sorted(FIXTURE_EXPORT.glob("*.json")):
        shutil.copy(src, export / src.name)
        data = (export / src.name).read_bytes()
        files[src.stem] = {"source_path": f"data/{src.stem}.ts", "source_sha256": "f" * 64,
                           "entries": len(json.loads(data)), "function_values_dropped": 0,
                           "output_sha256": hashlib.sha256(data).hexdigest()}
    (export / "export_report.json").write_text(json.dumps({"node_version": "vTEST", "sucrase_version": "0.0.0-test", "files": files}))
    (export.parent / "fetch_record.json").write_text(json.dumps({"license_files_sha256": {"LICENSE": "a" * 64}}))
    return export


def make_lock(path: Path, sha: str = FAKE_SHA):
    path.write_text(json.dumps({"repo": "file:///fixture", "ref": "fixture", "commit": sha}))
    return path


def build_fixture(root: Path, **kw):
    from data_pipeline.build import run_build
    make_cache(root)
    lock = make_lock(root / "lock.json")
    return run_build(root, root / "out", ENGINE_DIR, overrides_dir=ROOT / "data" / "overrides", lock_path=lock, **kw)
