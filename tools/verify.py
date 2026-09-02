#!/usr/bin/env python3
"""Verify the built PrusaSlicer 3.x offline bundles in ../dist.

Checks, per bundle:
  1. every YAML document parses
  2. no `values:` key falls outside the PrusaSlicer key allowlist (tools/prusa_keys.json)
  3. the per-version manifest.json lists every file except itself, with matching sha256
  4. the top-level layout matches the expected offline-repo shape

Exit code 0 = all pass, 1 = any failure.
"""
import os, io, sys, json, zipfile, hashlib
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = json.load(open(os.path.join(ROOT, "tools", "prusa_keys.json")))
KIND_ALLOW = {"print": "print", "printer": "printer", "filament": "filament",
              "tool_print": "tool"}  # kinds whose `values` we allowlist-check

def check(zn):
    print("=" * 70, "\n", os.path.basename(zn))
    z = zipfile.ZipFile(zn)
    names = z.namelist()
    ok = True
    if "manifest.json" not in names or "vendor_indices.zip" not in names:
        print("  !! missing top-level manifest.json / vendor_indices.zip"); return False
    vfiles = [n for n in names if n.endswith("/manifest.json")]
    if not vfiles:
        print("  !! missing per-version manifest.json"); return False
    base = vfiles[0][:-len("manifest.json")]
    print("  version dir:", base)
    vi = zipfile.ZipFile(io.BytesIO(z.read("vendor_indices.zip")))
    print("  idx:", vi.namelist())

    ndocs, badkeys = 0, []
    for n in [n for n in names if n.endswith(".yaml")]:
        try:
            docs = list(yaml.safe_load_all(io.TextIOWrapper(z.open(n), encoding="utf-8")))
        except Exception as e:
            print(f"  !! YAML parse error in {n}: {e}"); ok = False; continue
        for doc in docs:
            if not doc:
                continue
            ndocs += 1
            allow = KEYS.get(KIND_ALLOW.get(doc.get("kind")))
            def walk(node):
                if isinstance(node, dict):
                    v = node.get("values")
                    if isinstance(v, dict) and allow:
                        for k in v:
                            if k not in allow:
                                badkeys.append((n, doc.get("kind"), k))
                    for kk, vv in node.items():
                        if kk != "values":
                            walk(vv)
                elif isinstance(node, list):
                    for x in node:
                        walk(x)
            walk(doc)
    print(f"  yaml docs parsed: {ndocs}")
    if badkeys:
        ok = False
        print("  !! out-of-allowlist keys:", badkeys[:20], "total", len(badkeys))
    else:
        print("  key-safety: PASS")

    man = json.loads(z.read(base + "manifest.json"))
    listed = {e["filename"] for e in man}
    actual = {n[len(base):] for n in names if n.startswith(base) and n != base + "manifest.json"}
    miss, extra = actual - listed, listed - actual
    hashbad = [e["filename"] for e in man
               if hashlib.sha256(z.read(base + e["filename"])).hexdigest() != e["filehash"]]
    print(f"  manifest: listed={len(listed)} actual={len(actual)} "
          f"missing={miss or '-'} extra={extra or '-'} hashmismatch={hashbad or '-'}")
    if miss or extra or hashbad:
        ok = False
    return ok

if __name__ == "__main__":
    dist = os.path.join(ROOT, "dist")
    zips = sorted(os.path.join(dist, f) for f in os.listdir(dist) if f.endswith(".zip"))
    if not zips:
        print("no bundles in dist/ — run tools/anycubic_to_prusa.py first"); sys.exit(1)
    allok = all(check(z) for z in zips)
    print("\nOVERALL:", "PASS" if allok else "FAIL")
    sys.exit(0 if allok else 1)
