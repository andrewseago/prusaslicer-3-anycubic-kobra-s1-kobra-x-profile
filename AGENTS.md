# AGENTS.md — maintaining this repo

Guidance for future contributors and agents. This repo converts AnycubicSlicerNext
(OrcaSlicer/Bambu-lineage) profiles for the Anycubic Kobra S1 and Kobra X into
**PrusaSlicer 3.x** offline preset-repository bundles.

## Repo map

```
sources/anycubic/{machine,process,filament}/  vendored source profiles (both printers)
sources/anycubic/*.stl|*.svg|*.png            source bed model / texture / cover per printer
tools/anycubic_to_prusa.py                     the converter (edit conv_* maps here)
tools/verify.py                                validator (run before every commit)
tools/prusa_keys.json                          per-kind PrusaSlicer key allowlist (ground truth)
dist/*.zip                                      built bundles (regenerate; don't hand-edit)
```

Rebuild + validate after any change:
```bash
python3 tools/anycubic_to_prusa.py && python3 tools/verify.py   # must end: OVERALL: PASS
```
`verify.py` requires `pyyaml`. Commit `dist/` changes together with the tool/source changes.

## The two formats (the crux)

**Source — AnycubicSlicerNext** (Orca-lineage). Flat JSON under `machine/`, `process/`,
`filament/`. The Kobra S1/X profiles are **fully resolved** (`inherits: ""`) — every value is
present, so no inheritance chain resolution is needed. Orca vocabulary
(`sparse_infill_density`, `wall_loops`, `printable_area`, Klipper `G9111` start macro).
Beds: S1 = 250³ CoreXY; X = 260³ i3. When matching "Kobra S1", exclude "Kobra S1 Max".

**Target — PrusaSlicer 3.x offline repo** (what a `dist/*.zip` contains):
```
manifest.json            repo metadata: id / name / url / index_url / offline_archive_url / version:2
vendor_indices.zip       contains <Vendor>.idx  (idx basename == vendor folder == vendor.yaml id)
<Vendor>/<version>/
  vendor.yaml            multi-doc: kind: vendor | printer | tool | sheet | printer_config
  preset-printer-*.yaml  classic PrusaSlicer keys, NOT Orca keys
  preset-print-*.yaml
  preset-filament-*.yaml
  preset-tool-*.yaml
  assets/                bed .stl, texture .svg, thumbnail .png
  manifest.json          array of {filename, filehash(sha256)} for EVERY file in the version
                         dir EXCEPT manifest.json itself (all yaml + all assets)
```
Preset YAML shape: `id` (22-char base64 of 16 random bytes, `+`/`/` alphabet, no `=`), `kind`,
optional `inherits: ['*base*']`, and a nested `variants:` tree. **Leaf** nodes carry
`name` + `id` + `values`; base presets are named `*like_this*`. Print presets nest:
model condition → `tool.nozzle_diameter` condition → named quality leaves. **Retraction lives in
the print preset** (model-common values), not the printer preset.

## Translation rules (implemented in `tools/anycubic_to_prusa.py`)

1. **Map keys explicitly**; drop anything unmapped (Prusa rejects unknown keys). See the
   `conv_machine`, `conv_retraction`, `conv_process`, `conv_filament` functions. Examples:
   `sparse_infill_density→fill_density`, `wall_loops→perimeters`, `*_line_width→*_extrusion_width`,
   `outer_wall_speed→external_perimeter_speed`, `machine_max_speed_*→machine_max_feedrate_*`,
   `printable_area→bed_shape`, `retraction_length→retract_length`, `z_hop→retract_lift`,
   `nozzle_temperature→temperature`, `hot_plate_temp→bed_temperature`,
   `filament_flow_ratio→extrusion_multiplier`.
2. **Coerce values**: single-element Orca lists → scalar; machine limits (up to 3 Orca values)
   → Prusa 2-element `[normal, silent]`; `%`-accelerations resolved against `default_acceleration`;
   keep `%` only where Prusa accepts it (`fill_density`, `infill_overlap`, extrusion widths).
   Enum maps confirmed against the reference (`monotonicline→monotoniclines`, `zig-zag→rectilinear`,
   `auto_brim→outer_only`).
3. **Filter every emitted `values` key against `tools/prusa_keys.json[kind]`** — the safety net
   that keeps a stray Orca key from reaching the alpha parser. `verify.py` re-checks this.

## Design decisions baked in

- Scope = core set: all 4 nozzles, all Standard/High-Quality print profiles, and
  Generic + Anycubic PLA/PLA+/PETG/ABS/ASA/TPU filaments (see `FIL_TYPES`). Widen by editing
  `FIL_TYPES` — the full source set is already vendored under `sources/`.
- G-code rewritten to Prusa-native, `gcode_flavor: marlin2`. The `G9111` macro is replaced by
  `G28`+`G29`+prime line (`start_gcode`/`end_gcode` in the tool). `klipper` flavor was
  deliberately avoided — it is absent from the reference bundle, so its alpha acceptance is
  unverified. If you change these, note firmware compatibility.
- TPU → Prusa `FLEX` (`TPU` isn't in the reference type set).

## `tools/prusa_keys.json` (ground truth)

Extracted from Prusa's own `prusa-research-fff-offline` reference bundle — the authoritative set
of keys the 3.x parser accepts, per kind (`printer`/`print`/`filament`/`tool`). If a new
PrusaSlicer alpha adds/removes keys, regenerate it against the newer reference bundle:
```python
import glob,os,yaml,json,collections
D="path/to/PrusaResearch/<version>"; keys=collections.defaultdict(set)
def walk(n,k):
    if isinstance(n,dict):
        if isinstance(n.get('values'),dict): keys[k].update(n['values'])
        for kk,vv in n.items():
            if kk!='values': walk(vv,k)
    elif isinstance(n,list):
        for x in n: walk(x,k)
for f in glob.glob(D+"/preset-*.yaml"):
    b=os.path.basename(f)
    k='print' if b.startswith('preset-print-') else 'printer' if b.startswith('preset-printer-') else 'filament' if b.startswith('preset-filament-') else 'tool'
    for d in yaml.safe_load_all(open(f)):
        if d: walk(d,k)
json.dump({k:sorted(v) for k,v in keys.items()}, open('tools/prusa_keys.json','w'), indent=1)
```

## Invariants to keep (or the bundle won't load)

- **Printer `id` / `model.base_model` / `model.model` must be space-free tokens** (e.g. `KOBRAS1`,
  not `Kobra S1`). PrusaSlicer treats these as bare identifiers; a space makes the printer load
  without any error yet never appear in the Add Printer wizard. Every stock Prusa vendor uses
  space-free tokens (MINI, MK4, XL). Keep the human name in `name:` only. The converter enforces
  this via the `token` (identifier) vs `label` (display) split in `PRINTERS`.
- `vendor.yaml` `id` == `<Vendor>/` folder name == `<Vendor>.idx` basename.
- `default_print` (in `preset-printer`) must exactly match a print-leaf `name`.
- `default_material` (in the print `*common*` base) must exactly match a filament `name`.
- The per-version `manifest.json` must hash every file except itself; hashes must match.

## PrusaSlicer 3.0.0-alpha11 limitation (important)

The bundles are correct and pass Prusa's official schema, but **alpha11 will not show any
third-party vendor in the Add Printer picker**. In `PresetInteractor::load_preset_bundle`
the step that materializes printer configs is hardcoded to Prusa's own vendors:

```cpp
// TODO: remove this when config wizard is ready
for (const auto& vendor : {"PrusaResearch", "PrusaResearchSLA"}) { ... create_printer_config(...) }
```

Any other vendor loads without error but never gets `create_printer_config()`/`evaluate()`
called, so it produces zero printer presets and is invisible. This is still the case on
Prusa's `main` as of this writing — it should resolve when Prusa removes that block.

Workaround: `tools/merge_into_prusaresearch.py` merges the Kobra printers INTO the locally
installed PrusaResearch bundle (which the hardcoded loop does process), de-colliding our
`*common*`/`*PLA*`/... base ids. It is FRAGILE — PrusaResearch is an online repo and may be
re-synced/overwritten, reverting the merge; re-run the script (PrusaSlicer closed) to re-apply,
or `--revert` to undo. The standalone `dist/*.zip` bundles remain the correct long-term artifact.

## Gotchas

- These beds and profiles are best-effort; always print a calibration model after regenerating.
- Don't hand-edit `dist/`; regenerate so the manifest hashes stay consistent.
- No AI attribution in commits, docs, or code comments (repo convention).
