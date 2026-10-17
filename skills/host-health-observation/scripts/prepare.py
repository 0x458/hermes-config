#!/usr/bin/env python3
"""Copy the tested collector into a fresh user-selected task directory."""
import argparse
import json
import pathlib
import shutil

def prepare(workdir):
    root = pathlib.Path(workdir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workdir must be an existing directory")
    source = pathlib.Path(__file__).with_name("healthwatch.py")
    protected = ("healthwatch.py", "samples.jsonl", "runner.json", "completion.json", "jobs.json")
    if any((root / name).exists() for name in protected):
        raise FileExistsError("Task artifacts already exist; inspect or resume instead of overwriting.")
    if not shutil.which("systemctl"):
        raise RuntimeError("systemctl is unavailable on this host")
    target = root / "healthwatch.py"
    with target.open("x", encoding="utf-8") as out:
        out.write(source.read_text(encoding="utf-8"))
    target.chmod(0o600)
    print(json.dumps({"ready": True, "workdir": str(root), "collector": str(target)}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    prepare(parser.parse_args().workdir)
