"""Compare the selected desk results with the published data they depend on."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from services import cfg, read_services, alive
from phase2 import MODULES


def desk_state(root, inputs, receipt, trade_date):
    # Load the shared crowding dependency list, which also tracks publication
    # metadata and quality ledgers. Seven Desks adds its own formal inputs.
    app = str(root / "apps/vic_desks")
    if app not in sys.path:
        sys.path.insert(0, app)
    from crowding.page_cache import snapshot_fingerprint

    config = cfg(root)
    lake = Path(config["lake_root"])
    signatures = [snapshot_fingerprint(lake)]
    for layer, names in inputs.items():
        for name in names:
            for path in sorted((lake / layer / name).rglob("*.parquet")):
                stat = path.stat()
                signatures.append(f"{path.relative_to(lake)}:{stat.st_size}:{stat.st_mtime_ns}")

    # A published input or a committed change to the desk builders invalidates
    # a previously checked refresh, without depending on unrelated lake runs.
    signatures.append(subprocess.check_output(
        ["git", "rev-parse", "HEAD:apps/vic_desks", "HEAD:scripts/refresh_desks.py"],
        cwd=root, text=True,
    ))
    fingerprint = sha256("\n".join(signatures).encode()).hexdigest()
    versions = {name: config["phase3_versions"][name] for name in ("seven", "crowding")}
    directory = root / ".runtime/phase2/vic_desks"
    manifests_ok = all(
        (directory / name / "versions" / version / "manifest.json").is_file()
        for name, version in versions.items()
    )
    raw_path = root / ".runtime/desk-refresh/latest.json"
    build = json.loads(raw_path.read_text()) if raw_path.is_file() else {}
    candidates = build.get("build", {}).get("candidates", {})
    prior_versions = {name: candidates.get(name, {}).get("version") for name in versions}
    selected_date = build.get("build", {}).get("target_trading_date")
    refreshed = (build.get("status") == "completed" and manifests_ok and prior_versions == versions
                 and selected_date == trade_date)
    if refreshed:
        # A stopped product will read the selected version on its next start.
        # If it's already serving, require the actual API version to agree.
        from urllib.request import urlopen
        records = read_services(root)
        for name, version in versions.items():
            if name not in records or not alive(records[name], root):
                continue
            _, _, default_port, _, route = MODULES[name]
            port = config.get("phase2_ports", {}).get(name, default_port)
            try:
                with urlopen(f"http://127.0.0.1:{port}{route}", timeout=3) as response:
                    data = json.load(response)
                refreshed &= data.get("data_version", data.get("active_version")) == version
            except (OSError, ValueError):
                refreshed = False

    if build.get("status") == "completed" and prior_versions == versions and selected_date and selected_date > trade_date:
        return {"status": "superseded", "selected_versions": versions, "input_version": fingerprint,
                "refreshed_for_target": False, "selected_date": selected_date}
    return {
        "status": "complete" if refreshed and receipt.get("input_version") == fingerprint else "pending",
        "selected_versions": versions, "input_version": fingerprint,
        "refreshed_for_target": refreshed,
    }
