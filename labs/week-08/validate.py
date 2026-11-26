#!/usr/bin/env python3
"""Check an intent file against wic2005-intent.yang, with yanglint, before anything touches the network.

    python3 validate.py intent.yaml                     # exit 0 if valid, 1 if not
    python3 validate.py intent.yaml --emit-json x.json  # also write the JSON that was checked

The RULES live in your YANG module, not here. This script only translates intent.yaml into the
JSON encoding of your model (RFC 7951) and asks yanglint. Keep it that way: a rule checked in
Python instead of YANG doesn't count.

Week 8 assignment starter: finish to_json(). Run it in the yanglab container (pip install pyyaml).
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

import yaml

HERE = pathlib.Path(__file__).resolve().parent
MODULE = HERE / "wic2005-intent.yang"


def to_json(intent):
    """intent.yaml's structure -> RFC 7951 JSON for your module.

    Copy the values across as they are, even wrong ones: a VLAN 4095 in the YAML must reach
    yanglint as 4095, so that your model is what rejects it.
    """
    # TODO: build and return the JSON your module expects. The top-level name carries the module
    # name, as in the lab's example-vlans data: {"wic2005-intent:...": {...}}. Use the node names
    # from your own module (pyang -f tree shows them).
    raise NotImplementedError("to_json() isn't written yet")


def validate(path, emit=None):
    """(ok, message). Writes the JSON to `emit` first, valid or not, when asked to."""
    try:
        intent = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        return False, f"can't read {path}: {e}"
    if not isinstance(intent, dict):
        return False, f"{path} isn't a YAML mapping"
    data = json.dumps(to_json(intent), indent=2)
    if emit:
        pathlib.Path(emit).write_text(data + "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(data)
    try:
        p = subprocess.run(["yanglint", "-t", "config", str(MODULE), f.name], capture_output=True, text=True)
    finally:
        pathlib.Path(f.name).unlink()
    lines = [line for line in (p.stdout + p.stderr).splitlines() if not line.startswith("YANGLINT[E]")]
    return p.returncode == 0, "\n".join(lines).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("intent")
    ap.add_argument("--emit-json", metavar="FILE")
    a = ap.parse_args()
    ok, msg = validate(a.intent, a.emit_json)
    if ok:
        print(f"VALID: {a.intent} matches {MODULE.name}")
        return 0
    print(f"INVALID: {a.intent}")
    print(msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
