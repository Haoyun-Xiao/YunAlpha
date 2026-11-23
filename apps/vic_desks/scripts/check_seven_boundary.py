#!/usr/bin/env python3
"""Validate the documented migration boundary against the one-time source copy."""
import ast
from hashlib import sha256
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from seven_desks.store import code_hashes

EVIDENCE = APP / "evidence/seven"


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="旧源只读比对，不是运行时依赖")
    args = parser.parse_args()
    release = json.loads((EVIDENCE / "source-release.json").read_text())
    for relative, digest in release["source_hashes"].items():
        assert sha256((args.source / relative).read_bytes()).hexdigest() == digest, relative
    # Contrarian differs only by stripped trailing whitespace.
    for name in ("contrarian",):
        relative = f"seven_desks/adapters/{name}.py"
        assert [line.rstrip() for line in (APP / relative).read_text().splitlines()] == [line.rstrip() for line in (args.source / relative).read_text().splitlines()]
    market_old = ast.parse((args.source / "seven_desks/adapters/market.py").read_text())
    market_new = ast.parse((APP / "seven_desks/adapters/market.py").read_text())
    market_functions = lambda tree: {n.name: ast.dump(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert market_functions(market_old) == market_functions(market_new)
    original = ast.parse((args.source / "seven_desks/adapters/owners.py").read_text())
    migrated = ast.parse((APP / "seven_desks/adapters/owners.py").read_text())
    before = {node.name: node for node in original.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    after = {node.name: node for node in migrated.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert before.keys() == after.keys()
    altered = []
    for name in before:
        if ast.dump(before[name], include_attributes=False) != ast.dump(after[name], include_attributes=False):
            altered.append(name)
    assert sorted(altered) == ["_Data", "_meta"], altered
    # _Data initialization changes the file roots/connection; query logic and all other methods stay unchanged.
    old_methods = {n.name: n for n in before["_Data"].body if isinstance(n, ast.FunctionDef)}
    new_methods = {n.name: n for n in after["_Data"].body if isinstance(n, ast.FunctionDef)}
    assert old_methods.keys() == new_methods.keys()
    for name in old_methods.keys() - {"__init__"}:
        assert ast.dump(old_methods[name]) == ast.dump(new_methods[name]), name
    boundary = {
        "source_version": release["version"],
        "source_manifest_sha256": json.loads((EVIDENCE / "source-snapshot.json").read_text())["snapshot_manifest_sha256"],
        "unchanged_calculation_adapters": ["contrarian (only trailing whitespace normalized)", "market (all function ASTs; only fallback input path adapted)"],
        "owners_ast_unchanged": sorted(before.keys() - set(altered)),
        "owners_changed": {"_meta": "未传 Store 时使用同版冻结数据，不访问旧预览目录",
                           "_Data.__init__": "只改输入目录选择与 DuckDB 临时目录；注册 SQL 与其他查询方法保持"},
        "runtime_boundary": "仅 settings/store/server 及 owners 目录连接适配；业务计算、固定日期与版本保持",
        "runtime_hashes": code_hashes(),
    }
    (EVIDENCE / "migration-runtime.json").write_text(json.dumps(boundary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"source_files_verified":len(release["source_hashes"]), "unchanged_owners_definitions":len(before)-len(altered),
                      "runtime_modules":len(boundary["runtime_hashes"])},ensure_ascii=False))


if __name__ == "__main__":
    main()
