#!/usr/bin/env python3
"""WORKAROUND for PrusaSlicer 3.0.0-alpha11.

alpha11 only materializes printer configs for the hardcoded vendors
"PrusaResearch"/"PrusaResearchSLA" (PresetInteractor::load_preset_bundle,
`// TODO: remove this when config wizard is ready`), so a standalone third-party
vendor bundle never appears in Add Printer. This script merges the Kobra S1/X
printers INTO the locally-installed PrusaResearch bundle so that loop picks them up.

FRAGILE: PrusaSlicer may re-sync/overwrite PrusaResearch on update and revert this.
Re-run this script (with PrusaSlicer closed) to re-apply. Quit PrusaSlicer first.

Usage: python3 tools/merge_into_prusaresearch.py
       python3 tools/merge_into_prusaresearch.py --revert
"""
import os, re, sys, json, glob, zipfile, hashlib, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
# de-collide our base preset ids against PrusaResearch's identically-named bases
COLLIDE = ["common", "PLA", "PETG", "ABS", "ASA", "FLEX"]

def find_pr_dir():
    base = os.path.expanduser("~/Library/Application Support")
    hits = glob.glob(os.path.join(base, "PrusaSlicer*", "presets", "local",
                                  "prusa-research-fff", "PrusaResearch"))
    hits = [h for h in hits if os.path.exists(os.path.join(h, "vendor.yaml"))]
    if not hits:
        sys.exit("Could not find an installed PrusaResearch bundle "
                 "(presets/local/prusa-research-fff/PrusaResearch). Import a Prusa "
                 "config first, or check the app data dir.")
    return sorted(hits)[0]

MARK = "# --- anycubic-kobra merge (added by merge_into_prusaresearch.py) ---"

def our_slugs():
    return [os.path.basename(z)[:-len("-fff-offline.zip")] for z in
            glob.glob(os.path.join(DIST, "*-fff-offline.zip"))]

def rebuild_manifest(pr):
    entries = []
    for r, _, files in os.walk(pr):
        for fn in files:
            full = os.path.join(r, fn)
            rel = os.path.relpath(full, pr).replace(os.sep, "/")
            if rel == "manifest.json":
                continue
            entries.append({"filename": rel,
                            "filehash": hashlib.sha256(open(full, "rb").read()).hexdigest()})
    entries.sort(key=lambda e: e["filename"])
    json.dump(entries, open(os.path.join(pr, "manifest.json"), "w"))

def revert(pr):
    # remove files we added (kobras1/kobrax presets + our assets) and our vendor.yaml block
    for f in glob.glob(os.path.join(pr, "preset-*-kobra*.yaml")):
        os.remove(f)
    for f in glob.glob(os.path.join(pr, "assets", "AnycubicKobra*")):
        os.remove(f)
    vy = os.path.join(pr, "vendor.yaml")
    txt = open(vy).read()
    if MARK in txt:
        txt = txt[:txt.index(MARK)].rstrip() + "\n"
        open(vy, "w").write(txt)
    rebuild_manifest(pr)
    print("Reverted merge in", pr)

def merge(pr):
    vy_path = os.path.join(pr, "vendor.yaml")
    if not os.path.exists(vy_path + ".preAnycubic.bak"):
        shutil.copy(vy_path, vy_path + ".preAnycubic.bak")
    # start from a clean vendor.yaml (strip any prior merge block for idempotency)
    vy = open(vy_path).read()
    if MARK in vy:
        vy = vy[:vy.index(MARK)].rstrip() + "\n"
    added_docs = []
    os.makedirs(os.path.join(pr, "assets"), exist_ok=True)

    for zn in sorted(glob.glob(os.path.join(DIST, "*-fff-offline.zip"))):
        z = zipfile.ZipFile(zn)
        names = z.namelist()
        vyz = [n for n in names if n.endswith("vendor.yaml")][0]
        base = vyz[:-len("vendor.yaml")]                 # <Vendor>/<ver>/
        vtext = z.read(vyz).decode()
        # token from the printer doc id (e.g. KOBRAS1)
        tok = re.search(r"^kind: printer\ntechnology:.*?\nid: (\S+)", vtext, re.M)
        tok = tok.group(1) if tok else os.path.basename(zn).split("-")[2].upper()

        # keep only kind: printer / printer_config docs from our vendor.yaml
        for doc in vtext.split("\n---\n"):
            m = re.search(r"^kind:\s*(\S+)", doc, re.M)
            if m and m.group(1) in ("printer", "printer_config"):
                added_docs.append(doc.strip())

        # copy our preset files, de-colliding base ids
        for n in names:
            if not n.endswith(".yaml") or n.endswith("vendor.yaml"):
                continue
            content = z.read(n).decode()
            for c in COLLIDE:
                content = content.replace(f"'*{c}*'", f"'*{c}_{tok}*'")
            out = os.path.join(pr, os.path.basename(n))
            open(out, "w").write(content)
        # copy assets
        for n in names:
            if "/assets/" in n and not n.endswith("/"):
                data = z.read(n)
                open(os.path.join(pr, "assets", os.path.basename(n)), "wb").write(data)

    block = MARK + "\n" + "\n---\n".join(added_docs) + "\n"
    open(vy_path, "w").write(vy.rstrip() + "\n---\n" + block)
    rebuild_manifest(pr)
    print("Merged", ", ".join(our_slugs()), "into", pr)
    print("Backup of original vendor.yaml:", vy_path + ".preAnycubic.bak")

if __name__ == "__main__":
    pr = find_pr_dir()
    if "--revert" in sys.argv:
        revert(pr)
    else:
        merge(pr)
