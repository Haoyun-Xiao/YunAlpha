"""Migration boundary, uncached queries and isolated request cache."""
import json
from pathlib import Path

import pytest
from seven_desks import server, store
from seven_desks.settings import FROZEN_VERSION


def test_valid_uncached_query_keeps_frozen_data_and_writes_only_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CACHE", tmp_path)
    payload = json.loads(server.response("chips", "screen", {
        "date": "2026-11-09", "data_version": FROZEN_VERSION,
        "q": "600519", "page_size": "20", "sort": "symbol",
    }))
    assert payload["data_version"] == FROZEN_VERSION
    assert payload["sections"]
    paths = list(tmp_path.rglob("*.json"))
    assert len(paths) == 1 and "/pages/" in paths[0].as_posix()
    assert paths[0].read_bytes() == server.response("chips", "screen", {
        "date": "2026-11-09", "data_version": FROZEN_VERSION,
        "q": "600519", "page_size": "20", "sort": "symbol",
    })


def test_algorithm_guard_rejects_runtime_change_and_frozen_manifest_change(tmp_path, monkeypatch):
    original = server.PROCESS_CODE
    monkeypatch.setattr(server, "PROCESS_CODE", {**original, "seven_desks/adapters/market.py": "unexpected"})
    with pytest.raises(ValueError, match="运行计算边界已变化"):
        server.verify_algorithm(FROZEN_VERSION)
    monkeypatch.setattr(server, "PROCESS_CODE", original)
    bad = tmp_path / "manifest.json"
    bad.write_text('{"code_hashes":[]}')
    monkeypatch.setattr(server, "version_root", lambda version: tmp_path)
    with pytest.raises(ValueError, match="发布清单已变化"):
        server.verify_algorithm(FROZEN_VERSION)


def test_missing_file_never_falls_back_and_traversal_is_rejected():
    s = store.Store(FROZEN_VERSION)
    assert s.read("missing/missing.parquet") == []
    for bad in ("../outside", "/old-project/file.parquet"):
        with pytest.raises(ValueError):
            s.path(bad)
    for bad in (None, "../outside", "UNKNOWN"):
        with pytest.raises(ValueError):
            store.version_root(bad)


def test_crowding_entry_and_read_inputs_unchanged():
    project = Path(__file__).resolve().parents[1]
    assert "crowding) module=crowding.server" in (project / "run.sh").read_text()
    from crowding import settings
    assert settings.FROZEN_VERSION == "a8bbbe0793ac426c"


def test_candidate_clones_frozen_inputs_and_never_overwrites_active(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    writable = tmp_path / "runtime"
    base = inputs / "versions" / FROZEN_VERSION
    (base / "data/market").mkdir(parents=True)
    (base / "manifest.json").write_text(json.dumps({"data_version": FROZEN_VERSION, "valuation_cutoff": "2026-11-13"}))
    (base / "data/market/seed.json").write_text('{"value":1}')
    writable.mkdir()
    (writable / "active.json").write_text('{"data_version":"' + FROZEN_VERSION + '"}')
    monkeypatch.setattr(store, "FROZEN", inputs)
    monkeypatch.setattr(store, "CACHE", writable)
    result = store.create_version(FROZEN_VERSION)
    assert store.Store(result).valuation_cutoff == "2026-11-13"
    new_file = writable / "versions" / result / "data/market/seed.json"
    assert json.loads(new_file.read_text()) == {"value": 1}
    assert new_file.stat().st_ino != (base / "data/market/seed.json").stat().st_ino
    assert not new_file.is_symlink() and new_file.stat().st_nlink == 1
    assert json.loads((writable / "active.json").read_text())["data_version"] == FROZEN_VERSION
    assert json.loads((base / "data/market/seed.json").read_text()) == {"value": 1}
