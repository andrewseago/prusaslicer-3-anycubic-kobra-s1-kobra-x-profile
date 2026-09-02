# PrusaSlicer 3.x profiles — Anycubic Kobra S1 & Kobra X

Importable **PrusaSlicer 3.x** offline configuration bundles for the **Anycubic Kobra S1**
and **Anycubic Kobra X**, converted from the profiles that ship in AnycubicSlicerNext.

PrusaSlicer 3.x is in alpha preview and does not yet auto-import configurations from other
slicers, so these bundles package the printers, print-quality presets, and filament presets
in Prusa's own offline preset-repository format, ready to import.

## Download

Grab the bundle for your printer from [`dist/`](dist/):

| Printer | Bundle | Bed | Kinematics |
|---------|--------|-----|-----------|
| Anycubic Kobra S1 | [`anycubic-kobra-s1-fff-offline.zip`](dist/anycubic-kobra-s1-fff-offline.zip) | 250 × 250 × 250 mm | CoreXY |
| Anycubic Kobra X | [`anycubic-kobra-x-fff-offline.zip`](dist/anycubic-kobra-x-fff-offline.zip) | 260 × 260 × 260 mm | i3 (bed-slinger) |

Each bundle includes:

- **4 nozzle sizes** — 0.25, 0.4, 0.6, 0.8 mm
- **24 print-quality presets** — the full Standard / High-Quality set across all nozzles
- **7 filament presets** — Generic PLA plus Anycubic PLA, PLA+, PETG, ABS, ASA, TPU

## Install into PrusaSlicer 3.x

1. Open PrusaSlicer 3.x (alpha).
2. Use the offline configuration import flow (**Configuration → load / import an offline
   configuration archive**) and select the `.zip` for your printer. *(The exact menu wording
   may change between alpha builds; look for "offline" or "config archive".)*
3. Restart if prompted, then pick the printer, a print-quality preset, and a filament.

The archives use the same layout as Prusa's own `prusa-research-fff-offline.zip`, so they load
through the same mechanism.

## Important notes

- **Start/end G-code is Prusa-native**, not the stock Anycubic Klipper macros. Homing + mesh
  leveling is a generic `G28` + `G29`, followed by a fixed prime line; `gcode_flavor` is
  `marlin2` (Klipper firmware accepts the Marlin2 dialect). **Review the start G-code and adjust
  the leveling/prime to match your machine before printing.**
- **TPU** is mapped to PrusaSlicer's `FLEX` filament type (its equivalent).
- Scope is a curated **core set**. Decorative/composite filaments (Silk, Marble, Glow, Wood,
  Metal, CF, PA6-CF, etc.) are intentionally excluded; the full source profiles are in
  [`sources/`](sources/) if you want to widen the conversion.
- These profiles are a best-effort port. Print a calibration model and tune before committing to
  long prints. No warranty — see the license.

## Rebuild from source

The bundles are generated from the source profiles under [`sources/anycubic/`](sources/anycubic/).

```bash
python3 -m pip install pyyaml      # only dependency (for verify.py)
python3 tools/anycubic_to_prusa.py # writes dist/*.zip
python3 tools/verify.py            # validates the built bundles
```

To convert from a live AnycubicSlicerNext install instead of the vendored copy:

```bash
ANYCUBIC_SRC="$HOME/Library/Application Support/AnycubicSlicerNext/system/Anycubic" \
  python3 tools/anycubic_to_prusa.py
```

## Repository layout

```
dist/                 built, importable offline bundles (the downloads)
sources/anycubic/     source AnycubicSlicerNext profiles for both printers (machine/process/filament + bed assets)
tools/
  anycubic_to_prusa.py  the converter (Orca-lineage JSON -> PrusaSlicer 3.x YAML preset repo)
  verify.py             validates YAML, manifest hashes, structure, and key-safety
  prusa_keys.json       PrusaSlicer 3.x key allowlist (ground truth used to filter output)
AGENTS.md             format + translation reference for future contributors/agents
```

## How the conversion works

AnycubicSlicerNext is an OrcaSlicer/Bambu-lineage slicer; its profiles use Orca config keys
(`sparse_infill_density`, `wall_loops`, `printable_area`, …). PrusaSlicer uses a different
vocabulary (`fill_density`, `perimeters`, `bed_shape`, …) in a new YAML preset-repository
format. The converter maps keys explicitly, coerces value formats (list→scalar, machine limits
to `[normal, silent]`, `%`-relative accelerations resolved to absolute), rewrites G-code, and
**filters every emitted setting against `tools/prusa_keys.json`** so no unrecognized key can
reach the alpha's parser. Full details are in [`AGENTS.md`](AGENTS.md).

## Credits & license

Source printer/filament/process profiles and bed assets are from **Anycubic** (bundled with
AnycubicSlicerNext); all trademarks belong to their respective owners. The conversion tooling
and the generated Prusa-format bundles in this repository are released under the
[MIT License](LICENSE). This project is not affiliated with or endorsed by Anycubic or Prusa
Research.
