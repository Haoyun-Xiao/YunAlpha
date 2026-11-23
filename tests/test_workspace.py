"""Prevent loss of unmerged work and cross-worktree runtime contamination."""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


class WorkspaceLifecycle(unittest.TestCase):
    def test_isolation_and_retirement_guards(self):
        source = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="research-workspace-test-") as raw:
            base = pathlib.Path(raw)
            root = base / "repo"
            root.mkdir()
            shutil.copytree(source / "scripts", root / "scripts")
            shutil.copy2(source / ".gitignore", root / ".gitignore")
            (root / "config").mkdir()
            cfg = json.loads((source / "config/environment.example.json").read_text())
            cfg.update(worktree_root=str(base / "trees"), archive_root=str(base / "archive"), lake_root=str(base))
            (root / "config/environment.example.json").write_text(json.dumps(cfg))

            def command(*args, cwd=root, ok=True):
                p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
                if ok:
                    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                else:
                    self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                return p

            def ws(*args, cwd=root, ok=True):
                return command(sys.executable, str(cwd / "scripts/workspace.py"), *args, cwd=cwd, ok=ok)

            command("git", "init", "-b", "main")
            command("git", "config", "user.name", "Workspace Test")
            command("git", "config", "user.email", "workspace@example.invalid")
            command("git", "add", ".")
            command("git", "commit", "-m", "test baseline")
            ws("init")
            for name in ("a", "b"):
                ws("create", name, "--base", "main", "--task", name, "--scope", "docs")
            a, b = base / "trees/a", base / "trees/b"
            (a / ".runtime/state/value").write_text("A")
            self.assertFalse((b / ".runtime/state/value").exists())
            local = json.loads((a / "config/local.json").read_text())
            local["marker"] = "A"
            (a / "config/local.json").write_text(json.dumps(local))
            ws("init", cwd=a)
            self.assertEqual(json.loads((a / "config/local.json").read_text())["marker"], "A")
            self.assertNotIn("marker", json.loads((b / "config/local.json").read_text()))
            ws("create", "../outside", "--base", "main", "--task", "bad", "--scope", "docs", ok=False)
            (a / "change.txt").write_text("task work")
            ws("close", "a", ok=False)
            command("git", "add", "change.txt", cwd=a)
            command("git", "commit", "-m", "task work", cwd=a)
            ws("close", "a", ok=False)
            command("git", "merge", "--ff-only", "codex/a")
            ws("close", "a")
            self.assertFalse(a.exists())
            archived = list((base / "archive/worktree-retired").glob("a-*/.runtime/state/value"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(), "A")
            ws("close", "b")
            self.assertFalse(b.exists())
            self.assertEqual(len(command("git", "worktree", "list").stdout.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
