# Handoff: building a PrusaSlicer 3.x that shows third-party (non-Prusa) vendor profiles

**Audience:** an agent tasked with building/patching PrusaSlicer 3.x so that custom
(new-format YAML) vendor bundles actually appear in the "Add printer" UI.

**TL;DR:** The bundles produced by this repo are correct and pass Prusa's own schema, but
**PrusaSlicer 3.0.0-alpha11 hardcodes printer-config materialization to Prusa's own vendors**
(`PresetInteractor::load_preset_bundle`, `// TODO: remove this when config wizard is ready`).
Any other vendor loads without error but produces **zero** selectable printers. Remove/generalize
that gate and third-party bundles work. This doc gives the exact call chain, file:function
references, the fix, secondary issues, and how to test.

---

## 1. Environment (what was observed)

- App: `/Applications/PrusaSlicer-3.0.0-alpha11.app` (version string `3.0.0-alpha11`).
- Data dir: `~/Library/Application Support/PrusaSlicer3-dev/`
  - New YAML preset repos (installed): `presets/local/<repo-id>/<Vendor>/{vendor.yaml, preset-*.yaml, assets/, manifest.json}`
  - Live repo registry: `shared_runtime/RepositoryManifest.json` (has `selected`, `uuid`, `unzipped_data_path`).
  - `ArchiveRepositoryManifest.json` (data-dir root): appears **stale/legacy** — lists only the 4 built-in
    Prusa repos; not updated by offline imports. The live DB is the `shared_runtime` one (see
    `PresetUpdaterRepositoryDatabase.cpp`, path built at `data_dir()/shared_runtime/RepositoryManifest.json`).
  - Imported offline archive staging: `local_repositories/<uuid>/`
  - Legacy `.ini` vendor bundles: `vendor/*.ini` (+ `.idx`) — used by the LEGACY wizard only.
  - App runtime log (very useful): `shared_runtime/log.txt`.
  - Bundle cache: `cache/bundle_cache` — only used when built with `SLIC3R_DEBUG_PRESET_CACHE`
    (not present in this alpha build; a fresh load path runs every launch).
- Source: `github.com/prusa3d/PrusaSlicer`. The new preset system lives under
  `src/slic3r-domain/` and `src/slic3r-shared/`. **The hardcoded gate below is still present on
  `main` as of 2026-09.**

---

## 2. THE ROOT CAUSE (fix this)

File: `src/slic3r-shared/src/Slic3r/Biz/Preset/PresetInteractor.cpp`
Function: `PresetInteractor::load_preset_bundle(const IO::BundlePaths&)` (~line 298).

Inside, after `load_bundle()` populates `preset_bundle.vendor_bundles`, this block materializes the
concrete `printer_configs` that the wizard shows — and it is hardcoded to two Prusa vendor ids:

```cpp
// TODO: remove this when config wizard is ready
{
    HwConfigEvaluator config_eval;
    for (const auto& vendor : {"PrusaResearch", "PrusaResearchSLA"}) {   // <-- HARDCODED GATE
        auto vendor_bundle_it = preset_bundle.vendor_bundles.find(vendor);
        if (vendor_bundle_it == preset_bundle.vendor_bundles.end()
            || std::ranges::any_of(preset_bundle.printer_configs | std::views::values,
                                   [&](const auto& hw){ return hw.vendor_id == vendor; }))
            continue;
        auto& vendor_bundle = vendor_bundle_it->second;
        for (const auto& hw_printer_template : vendor_bundle.vendor_data.printer_configs) {
            auto printer_config = config_eval.create_printer_config(hw_printer_template,
                                                                    vendor_bundle.vendor_data);
            preset_bundle.printer_configs.emplace(printer_config.id, printer_config);
            vendor_bundle.printer_configs.emplace_back(std::move(printer_config));
        }
    }
}
```

Because `create_printer_config()` is only ever called for `PrusaResearch`/`PrusaResearchSLA`,
every other vendor's `vendor_bundle.printer_configs` stays **empty**. Downstream that means no
printer presets are produced and nothing shows — with **no error logged** (see call chain).

### The fix (generalize the loop to all vendors)

Replace the two-element `{"PrusaResearch","PrusaResearchSLA"}` list with iteration over **all**
loaded vendor bundles:

```cpp
{
    HwConfigEvaluator config_eval;
    for (auto& [vendor_id, vendor_bundle] : preset_bundle.vendor_bundles) {
        if (std::ranges::any_of(preset_bundle.printer_configs | std::views::values,
                                [&](const auto& hw){ return hw.vendor_id == vendor_id; }))
            continue;  // already materialized (e.g. restored from cache)
        for (const auto& tmpl : vendor_bundle.vendor_data.printer_configs) {
            auto pc = config_eval.create_printer_config(tmpl, vendor_bundle.vendor_data);
            preset_bundle.printer_configs.emplace(pc.id, pc);
            vendor_bundle.printer_configs.emplace_back(std::move(pc));
        }
    }
}
```

Then `update_vendor_presets(...)` (already looped over all vendor ids at ~line 350) will call
`evaluate()` for every vendor's now-populated `printer_configs`, producing printer presets for all.

---

## 3. Full data-flow / call chain (with file:function references)

Load → materialize → evaluate → list in wizard:

1. `PresetInteractor::load_preset_bundle` (PresetInteractor.cpp ~298)
   - `IO::load_bundle(bundle_paths)` — `src/slic3r-shared/.../IO/BundleLoader.cpp::load_bundle` (~65).
     Iterates `{local_bundle_path, app_bundle_path}`, finds `<repo>/<vendor>/vendor.yaml`, parses it via
     `HwConfigLoader::load` (vendor/printer/tool/sheet/printer_config templates) and preset YAMLs via
     `PresetLoader::load_dir` (**origin defaults to `PresetOrigin::System`**, BundleLoader.cpp ~115;
     user-dir presets loaded as `PresetOrigin::User` at ~133). Result stored in
     `bundle.vendor_bundles[vendor_id]`.
   - **THE GATE** (section 2) — only Prusa vendors get `create_printer_config()`.
   - `update_vendor_presets(mut, preset_bundle, vendor_id)` for each vendor id (~350) →
     evaluates over `vendor_bundle.printer_configs` (PresetInteractor.cpp ~234-244). Empty for non-Prusa.
2. `HwConfigEvaluator::create_printer_config` — `src/slic3r-shared/.../HwConfigEvaluator.cpp` (~79).
   Resolves the `printer_config` template into an `HwPrinterConfig`: looks up the `kind:printer` def by
   `templ.printer` (`VendorData::find_printer_config_def_by_id`, HwConfig.cpp ~102), the tools by id,
   the sheet by id (or `first_compatible_sheet`). Uses ASSERTs (no-ops in release). `from_def` (~60)
   sets name/short_name/tool_count/legacy_printer_model — **verify it also carries `visual`
   (bed_model/bed_texture/thumbnail)**; bed + thumbnail rendered in testing, so visual is propagated,
   but confirm when refactoring.
3. `PresetEvaluator::evaluate(const HwPrinterConfig&)` — `src/slic3r-shared/.../PresetEvaluator.cpp` (~507).
   `eval_preset()` matches printer-kind presets against the hw_config context; **line ~528**:
   `if (printer_presets.empty()) SPDLOG_ERROR("No printer presets available for configuration ...")`.
   (In testing this error never fired for the custom vendor — proving `evaluate()` was never reached
   because `printer_configs` was empty, i.e. the gate, not a preset-matching failure.)
   - `preset_from_context` (~397) copies `origin` from the source preset node.
   - Invalid-key handling (~312): `SPDLOG_ERROR("Invalid key {} ...")` then `continue` — **non-fatal**
     (the `top_one_perimeter_type ... ToolPrintSettings` spam in logs is Prusa's OWN bundle; ignore it).
4. `PresetInteractor::fill_printer_presets` (~1559) builds the UI list from `printer_presets()`.
5. `AddPrinterPanel::reload` — `src/slic3r-shared/src/Slic3r/App/AddPrinterPanel.cpp` (~215).
   Iterates `preset_interactor().printer_presets().items()` **filtered to `origin==PresetOrigin::System`**
   (filter set at ~151-153), groups by `config.model.base_model`, renders one card per printer preset
   (thumbnail via `get_thumbnail` ~29 → `config.relative_path_to_assets()+visual.thumbnail`).
   - **Note:** the left "source" column is a hardcoded `LayoutButton("Prusa3D")` (AddPrinterPanel.cpp
     **~133**) — purely cosmetic in alpha11, NOT a vendor filter. Make it dynamic once multiple vendors show.
   - Opened via `PrinterAddDialog` (title "Add printer") from the sidebar (`SidebarBed.cpp`,
     `LogicalPrinterSettingsDialog`), **not** the legacy Configuration Assistant.

`PresetOrigin` enum: `src/slic3r-domain/include/Slic3r/Domain/Preset/PresetTree.hpp` → `{System, User,
Runtime}`, default `System`.

---

## 4. Two separate "Add Printer" UIs (don't confuse them)

- **New (YAML-aware):** `PrinterAddDialog` → `AddPrinterPanel` (`src/slic3r-shared/.../App/`). Reads
  the new preset system (`printer_presets()`), i.e. what the gate feeds. This is the one to make work.
- **Legacy (INI-only):** `src/slic3r/GUI/ConfigWizard.cpp` (~133-209). Loads classic `.ini` vendor
  bundles from `data_dir/vendor/*.ini`, `data_dir/cache/vendor`, and `resources/profiles`
  (`VendorProfile::from_ini`), filtered by `preset_updater_wrapper->is_selected_repository_by_id(vp.repo_id)`.
  It will never show new-format YAML vendors. (alpha11 ships **no** `.ini` in `resources/profiles`;
  it ships YAML in `resources/presets/`.)

---

## 5. Authoritative schema + a minimal known-good fixture

- Schema: `specs/presets/vendor-schema.json` — validate any `vendor.yaml` against this. Note:
  `id`/`base_model`/`model` have **no pattern constraint** in the schema, but PrusaSlicer's model
  grouping/conditions treat them as bare tokens — **use space-free ids** (e.g. `KOBRAS1`, not
  "Kobra S1"). `thumbnail` is just a string; **PNG works** (SVG-wrapping-a-raster does NOT render in
  the card — use a real PNG or a true vector SVG).
- Minimal fixture bundle: `src/slic3r-shared/test/data/preset updater/server100/test_repo/TestVendor/1.0.0/`
  (`vendor.yaml` + `printer.yaml` + `manifest.json`). Good template/reference.

---

## 6. Test artifacts in THIS repo (use them to validate the build)

- `dist/anycubic-kobra-s1-fff-offline.zip`, `dist/anycubic-kobra-x-fff-offline.zip` — correct,
  schema-valid new-format offline bundles (standalone, separate repo ids). Use these as the
  third-party test vendor.
- `tools/anycubic_to_prusa.py` — regenerates them from `sources/anycubic/`.
- `tools/verify.py` — validates YAML parse, manifest sha256, structure, key allowlist.
- `tools/prusa_keys.json` — per-kind valid-key allowlist extracted from Prusa's own bundle.
- Bundle layout (matches Prusa's `prusa-research-fff-offline.zip`):
  `manifest.json` + `vendor_indices.zip` (→ `<Vendor>.idx`) + `<Vendor>/<ver>/{vendor.yaml,
  preset-printer/print/filament/tool-*.yaml, assets/, manifest.json}`. Per-version `manifest.json`
  sha256s every file except itself. Invariant: `vendor.yaml` id == `<Vendor>/` folder == `<Vendor>.idx`
  basename. `default_print` must match a print-leaf `name`; `default_material` must match a filament `name`.

### How to verify the build fix end-to-end
1. Build the patched PrusaSlicer.
2. Import `dist/anycubic-kobra-s1-fff-offline.zip` and `...-x-...zip` via the offline-archive import.
3. Sidebar → printer button → **Add printer** → search "Kobra".
4. PASS = `KOBRAS1` and `KOBRAX` families appear (entries "Anycubic Kobra S1"/"Anycubic Kobra X",
   nozzles 0.25/0.4/0.6/0.8) **without** the merge workaround below.
5. Cross-check `shared_runtime/log.txt` for `PresetEvaluator.cpp:... No printer presets available`
   (should NOT appear for the Kobra configs once the gate is removed).

---

## 7. The temporary workaround we used (context; NOT a build fix)

`tools/merge_into_prusaresearch.py` injects the Kobra printer/printer_config docs + preset files +
assets **into the locally-installed PrusaResearch bundle** (the one vendor the gate processes),
de-colliding base ids (`*common*`→`*common_S1*`, `*PLA*`→`*PLA_S1*`, …) and reusing Prusa's `0.4`
tool + `pei_textured` sheet. It works but is **fragile** (PrusaResearch is an online repo; a re-sync
reverts it) and it caused a launch crash once (see next). `--revert` undoes it. Once the gate is
removed in the build, this workaround is unnecessary — use the standalone `dist/*.zip`.

---

## 8. Secondary issues the build agent should also address

1. **Crash on restart when a printer's tool set changes under a saved selection.** We disabled
   `supports_high_flow_nozzle` on a printer that was the *active* selection as "0.8 HF"; on next launch
   PrusaSlicer crashed restoring the now-invalid tool. Session-restore should tolerate a selected
   tool/sheet/printer that no longer resolves (fall back gracefully) instead of crashing.
2. **Default sheet = "Cold".** `LogicalPrinterSettingsDialog.cpp` (~399-415) builds the Sheet combo
   from `sheet_items()` but the initial selection does not reliably honor the `printer_config.sheet`
   (we set `pei_textured`; UI showed "Cold"). Make the combo default to the config's sheet.
3. **`create_printer_config`/`from_def` visual propagation** (HwConfigEvaluator.cpp ~60) — confirm
   `visual` (bed_model/bed_texture/thumbnail) is copied so the viewport bed + card render for all vendors.
4. **Hardcoded "Prusa3D" source label** (AddPrinterPanel.cpp ~133) — make the left column reflect the
   actual vendor(s) once more than one is present.

---

## 9. Key source files (roles)

| Path | Role |
|---|---|
| `src/slic3r-shared/src/Slic3r/Biz/Preset/PresetInteractor.cpp` | **The gate** (`load_preset_bundle`); `update_vendor_presets`, `fill_printer_presets` |
| `src/slic3r-shared/src/Slic3r/Biz/Preset/HwConfigEvaluator.cpp` | `create_printer_config`, `from_def`, `first_compatible_sheet` |
| `src/slic3r-shared/src/Slic3r/Biz/Preset/PresetEvaluator.cpp` | `evaluate(hw_config)`, `preset_from_context`, invalid-key skip |
| `src/slic3r-shared/src/Slic3r/Biz/Preset/IO/BundleLoader.cpp` | `load_bundle`, `populate_local_bundle`, origin assignment |
| `src/slic3r-shared/src/Slic3r/Biz/Preset/IO/HwConfigLoader.cpp` | parses vendor.yaml → defs + printer_config templates |
| `src/slic3r-domain/src/Slic3r/Domain/Preset/HwConfig.cpp` | `find_*_def_by_id`, `relative_path_to_assets()` |
| `src/slic3r-domain/src/Slic3r/Domain/Preset/Bundle.cpp` | evaluated-preset lookups |
| `src/slic3r-domain/include/Slic3r/Domain/Preset/PresetTree.hpp` | `PresetOrigin {System,User,Runtime}` |
| `src/slic3r-shared/src/Slic3r/App/AddPrinterPanel.cpp` | new Add Printer picker (origin==System, group by base_model) |
| `src/slic3r-shared/src/Slic3r/App/PrinterAddDialog.cpp` / `SidebarBed.cpp` / `LogicalPrinterSettingsDialog.cpp` | how the picker is opened; Sheet/Nozzle detail |
| `src/slic3r/GUI/ConfigWizard.cpp` | legacy `.ini`-only wizard (not YAML-aware) |
| `src/slic3r-shared/src/Slic3r/Biz/PresetUpdater/PresetUpdaterRepositoryDatabase.cpp` | repo registry (`RepositoryManifest.json`), `selected`/`has_installed_printers` |
| `specs/presets/vendor-schema.json` | vendor.yaml schema |
| `src/slic3r-shared/test/data/preset updater/.../TestVendor/` | minimal fixture bundle |

---

## 10. One-line summary for the build

In `PresetInteractor::load_preset_bundle`, delete the `// TODO: remove this when config wizard is
ready` restriction and materialize `printer_configs` for **all** `vendor_bundles`, not just
`PrusaResearch`/`PrusaResearchSLA`. Then verify with this repo's `dist/*.zip` that `KOBRAS1`/`KOBRAX`
appear in Add Printer with no merge hack.
