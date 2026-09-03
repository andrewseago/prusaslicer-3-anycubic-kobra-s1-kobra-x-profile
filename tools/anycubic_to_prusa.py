#!/usr/bin/env python3
"""Convert AnycubicSlicerNext (Orca-lineage) profiles for the Kobra S1 / Kobra X
into PrusaSlicer 3.x offline preset-repository bundles.

Reads source profiles from ../sources/anycubic (override with ANYCUBIC_SRC), and
writes ../dist/anycubic-kobra-{s1,x}-fff-offline.zip.

See AGENTS.md for the format/translation reference.
"""
import os, re, json, glob, base64, hashlib, shutil, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get("ANYCUBIC_SRC", os.path.join(ROOT, "sources", "anycubic"))
OUT = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, ".build")
KEYS = json.load(open(os.path.join(ROOT, "tools", "prusa_keys.json")))  # per-kind allowlist

# ---- printer-specific config -------------------------------------------------
# NOTE: `token` (base_model / model / printer id) must be a space-free identifier —
# PrusaSlicer's condition expressions and model lookup treat it as a bare token, so a
# value with a space ("Kobra S1") silently breaks matching and hides the printer.
# `label` is the human-facing name shown in the wizard and preset names.
PRINTERS = {
    "s1": dict(
        src_name="Anycubic Kobra S1", label="Kobra S1", token="KOBRAS1",
        vendor_id="AnycubicKobraS1",
        repo_id="anycubic-kobra-s1-fff", repo_name="Anycubic Kobra S1 FFF",
        zip_name="anycubic-kobra-s1-fff-offline.zip", bed=250, park_y=240,
    ),
    "x": dict(
        src_name="Anycubic Kobra X", label="Kobra X", token="KOBRAX",
        vendor_id="AnycubicKobraX",
        repo_id="anycubic-kobra-x-fff", repo_name="Anycubic Kobra X FFF",
        zip_name="anycubic-kobra-x-fff-offline.zip", bed=260, park_y=250,
    ),
}
NOZZLES = ["0.25", "0.4", "0.6", "0.8"]
# core filament material types to include (clean prefix -> Prusa filament_type)
FIL_TYPES = {
    "Generic PLA": "PLA", "Anycubic PLA+": "PLA", "Anycubic PLA": "PLA",
    "Anycubic PETG": "PETG", "Anycubic ABS": "ABS", "Anycubic ASA": "ASA",
    "Anycubic TPU 95A": "FLEX", "Anycubic TPU": "FLEX",
}

def gid():
    return base64.b64encode(os.urandom(16)).decode().rstrip("=")

def num(v):
    s = str(v).strip()
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except ValueError:
        return s

def first(v):
    return v[0] if isinstance(v, list) and v else v

# ---------------- G-code (rewritten Prusa-native, Marlin2 dialect) ------------
def start_gcode(P):
    return ("M140 S[first_layer_bed_temperature] ; set bed temp\n"
            "M104 S[first_layer_temperature] ; set nozzle temp\n"
            "M190 S[first_layer_bed_temperature] ; wait for bed temp\n"
            "M109 S[first_layer_temperature] ; wait for nozzle temp\n"
            "G90 ; absolute positioning\n"
            "M83 ; relative extrusion\n"
            "G28 ; home all axes\n"
            "G29 ; mesh bed leveling\n"
            "G92 E0 ; reset extruder\n"
            "G1 Z2.0 F3000 ; lift\n"
            "; prime line\n"
            "G1 X5 Y20 Z0.3 F5000\n"
            "G1 X5 Y150 E15 F1500\n"
            "G1 X5.4 Y150 Z0.3 F5000\n"
            "G1 X5.4 Y20 E30 F1500\n"
            "G92 E0 ; reset extruder")

def end_gcode(P):
    return ("M104 S0 ; turn off nozzle\n"
            "M140 S0 ; turn off bed\n"
            "M107 ; turn off fan\n"
            "G91 ; relative positioning\n"
            "G1 E-2 F3000 ; retract\n"
            "G1 Z10 F600 ; lift\n"
            "G90 ; absolute positioning\n"
            "G1 X5 Y%d F6000 ; park\n" % P["park_y"] +
            "M84 ; disable motors")

# ---------------- machine -> printer preset values ---------------------------
def conv_machine(m, P):
    v = {}
    v["printer_technology"] = "FFF"
    v["bed_shape"] = m.get("printable_area", ["0x0", "%dx0" % P["bed"],
                                              "%dx%d" % (P["bed"], P["bed"]), "0x%d" % P["bed"]])
    v["max_print_height"] = num(m.get("printable_height", P["bed"]))
    v["gcode_flavor"] = "marlin2"
    v["single_extruder_multi_material"] = 0
    v["use_relative_e_distances"] = 1
    v["use_firmware_retraction"] = 0
    v["use_volumetric_e"] = 0
    v["variable_layer_height"] = 1
    v["silent_mode"] = num(m.get("silent_mode", 0))
    v["remaining_times"] = 0
    v["z_offset"] = num(first(m.get("z_offset", 0)))
    v["extruder_offset"] = ["0x0"]
    v["machine_limits_usage"] = "emit_to_gcode"
    v["thumbnails"] = "320x240/PNG"
    v["start_gcode"] = start_gcode(P)
    v["end_gcode"] = end_gcode(P)
    v["before_layer_gcode"] = ""
    v["layer_gcode"] = ";AFTER_LAYER_CHANGE\n;[layer_z]"
    def lim(key):
        a = m.get(key, [])
        a = [num(x) for x in a] if isinstance(a, list) else [num(a)]
        if not a: return None
        return [a[0], a[1] if len(a) > 1 else a[0]]
    limit_map = {
        "machine_max_acceleration_x": "machine_max_acceleration_x",
        "machine_max_acceleration_y": "machine_max_acceleration_y",
        "machine_max_acceleration_z": "machine_max_acceleration_z",
        "machine_max_acceleration_e": "machine_max_acceleration_e",
        "machine_max_acceleration_extruding": "machine_max_acceleration_extruding",
        "machine_max_acceleration_retracting": "machine_max_acceleration_retracting",
        "machine_max_acceleration_travel": "machine_max_acceleration_travel",
        "machine_max_speed_x": "machine_max_feedrate_x",
        "machine_max_speed_y": "machine_max_feedrate_y",
        "machine_max_speed_z": "machine_max_feedrate_z",
        "machine_max_speed_e": "machine_max_feedrate_e",
        "machine_max_jerk_x": "machine_max_jerk_x",
        "machine_max_jerk_y": "machine_max_jerk_y",
        "machine_max_jerk_z": "machine_max_jerk_z",
        "machine_max_jerk_e": "machine_max_jerk_e",
        "machine_min_extruding_rate": "machine_min_extruding_rate",
        "machine_min_travel_rate": "machine_min_travel_rate",
    }
    for ok, pk in limit_map.items():
        r = lim(ok)
        if r is not None:
            v[pk] = r
    return {k: val for k, val in v.items() if k in KEYS["printer"]}

def conv_retraction(m):
    v = {}
    v["retract_length"] = num(first(m.get("retraction_length", 0.8)))
    v["retract_speed"] = num(first(m.get("retraction_speed", 40)))
    ds = num(first(m.get("deretraction_speed", 0)))
    v["deretract_speed"] = ds if ds else num(first(m.get("retraction_speed", 40)))
    v["retract_before_travel"] = num(first(m.get("retraction_minimum_travel", 1)))
    v["retract_lift"] = num(first(m.get("z_hop", 0.4)))
    v["retract_layer_change"] = num(first(m.get("retract_when_changing_layer", 1)))
    v["retract_restart_extra"] = num(first(m.get("retract_restart_extra", 0)))
    v["retract_before_wipe"] = 0
    v["wipe"] = num(first(m.get("wipe", 1)))
    v["retract_lift_above"] = num(first(m.get("retract_lift_above", 0)))
    return {k: val for k, val in v.items() if k in KEYS["print"]}

# ---------------- process -> print preset values -----------------------------
def pct_or_num(v, base):
    s = str(v).strip()
    if s.endswith("%"):
        try: return round(float(s[:-1]) / 100.0 * base, 2)
        except ValueError: return base
    return num(s)

FILL = {"grid":"grid","gyroid":"gyroid","honeycomb":"honeycomb","3dhoneycomb":"3dhoneycomb",
        "cubic":"cubic","adaptivecubic":"adaptivecubic","supportcubic":"supportcubic",
        "line":"line","concentric":"concentric","triangles":"triangles","lightning":"lightning",
        "rectilinear":"rectilinear","zig-zag":"rectilinear","monotonic":"monotonic",
        "monotonicline":"monotoniclines","tri-hexagon":"stars"}
TOPFILL = {"monotonic":"monotonic","monotonicline":"monotoniclines","concentric":"concentric",
           "zig-zag":"rectilinear","rectilinear":"rectilinear","monotoniclines":"monotoniclines"}
BRIM = {"auto_brim":"outer_only","brim_ears":"outer_only","outer_only":"outer_only",
        "outer_and_inner":"outer_and_inner","inner_only":"inner_only","no_brim":"no_brim","":"no_brim"}

def conv_process(p):
    dflt_acc = num(p.get("default_acceleration", 5000)) or 5000
    v = {}
    def s(k, val):
        if val is not None: v[k] = val
    s("layer_height", num(p.get("layer_height")))
    s("first_layer_height", num(p.get("initial_layer_print_height")))
    s("perimeters", num(p.get("wall_loops")))
    s("top_solid_layers", num(p.get("top_shell_layers")))
    s("bottom_solid_layers", num(p.get("bottom_shell_layers")))
    s("top_solid_min_thickness", num(p.get("top_shell_thickness")))
    s("bottom_solid_min_thickness", num(p.get("bottom_shell_thickness")))
    s("fill_density", str(p.get("sparse_infill_density", "15%")))
    s("fill_pattern", FILL.get(p.get("sparse_infill_pattern","grid"),"grid"))
    s("top_fill_pattern", TOPFILL.get(p.get("top_surface_pattern","monotonic"),"monotonic"))
    s("bottom_fill_pattern", TOPFILL.get(p.get("bottom_surface_pattern","monotonic"),"monotonic"))
    s("fill_angle", num(p.get("infill_direction", 45)))
    s("infill_overlap", str(p.get("infill_wall_overlap","15%")))
    s("infill_every_layers", 1)
    s("infill_anchor", pct_or_num(p.get("infill_anchor","2.5"), 2.5))
    s("infill_anchor_max", num(p.get("infill_anchor_max", 12)))
    s("solid_infill_below_area", num(p.get("minimum_sparse_infill_area", 0)))
    s("extrusion_width", num(p.get("line_width")))
    s("perimeter_extrusion_width", num(p.get("inner_wall_line_width")))
    s("external_perimeter_extrusion_width", num(p.get("outer_wall_line_width")))
    s("infill_extrusion_width", num(p.get("sparse_infill_line_width")))
    s("solid_infill_extrusion_width", num(p.get("internal_solid_infill_line_width")))
    s("top_infill_extrusion_width", num(p.get("top_surface_line_width")))
    s("first_layer_extrusion_width", num(p.get("initial_layer_line_width")))
    s("support_material_extrusion_width", num(p.get("support_line_width")))
    s("external_perimeter_speed", num(p.get("outer_wall_speed")))
    s("perimeter_speed", num(p.get("inner_wall_speed")))
    s("infill_speed", num(p.get("sparse_infill_speed")))
    s("solid_infill_speed", num(p.get("internal_solid_infill_speed")))
    s("top_solid_infill_speed", num(p.get("top_surface_speed")))
    s("first_layer_speed", num(p.get("initial_layer_speed")))
    s("first_layer_solid_infill_speed", num(p.get("initial_layer_infill_speed")))
    s("travel_speed", num(p.get("travel_speed")))
    s("travel_speed_z", num(p.get("travel_speed_z", 0)))
    s("bridge_speed", num(p.get("bridge_speed")))
    s("gap_fill_speed", num(p.get("gap_infill_speed")))
    s("support_material_speed", num(p.get("support_speed")))
    s("support_material_interface_speed", num(p.get("support_interface_speed", 80)))
    sp = p.get("small_perimeter_speed","50%")
    s("small_perimeter_speed", str(sp) if str(sp).endswith("%") else num(sp))
    s("max_print_speed", num(p.get("max_print_speed", 200)))
    s("default_acceleration", dflt_acc)
    s("external_perimeter_acceleration", pct_or_num(p.get("outer_wall_acceleration", dflt_acc), dflt_acc))
    s("perimeter_acceleration", pct_or_num(p.get("inner_wall_acceleration", dflt_acc), dflt_acc))
    s("infill_acceleration", pct_or_num(p.get("sparse_infill_acceleration", dflt_acc), dflt_acc))
    s("solid_infill_acceleration", pct_or_num(p.get("internal_solid_infill_acceleration", dflt_acc), dflt_acc))
    s("top_solid_infill_acceleration", pct_or_num(p.get("top_surface_acceleration", dflt_acc), dflt_acc))
    s("first_layer_acceleration", pct_or_num(p.get("initial_layer_acceleration", 500), dflt_acc))
    s("travel_acceleration", pct_or_num(p.get("travel_acceleration", dflt_acc), dflt_acc))
    s("bridge_acceleration", pct_or_num(p.get("bridge_acceleration", dflt_acc), dflt_acc))
    s("skirts", num(p.get("skirt_loops", 0)))
    s("skirt_distance", num(p.get("skirt_distance", 2)))
    s("skirt_height", num(p.get("skirt_height", 1)))
    s("brim_width", num(p.get("brim_width", 0)))
    s("brim_separation", num(p.get("brim_object_gap", 0.1)))
    s("elefant_foot_compensation", num(p.get("elefant_foot_compensation", 0)))
    s("seam_position", p.get("seam_position","aligned") if p.get("seam_position","aligned") in
      ("aligned","nearest","rear","random") else "aligned")
    s("bridge_flow_ratio", num(p.get("bridge_flow", 1)))
    s("overhangs", num(p.get("detect_overhang_wall", 1)))
    s("thin_walls", num(p.get("detect_thin_wall", 0)))
    s("gap_fill_enabled", 0 if num(p.get("filter_out_gap_fill","0"))==1 else 1)
    s("perimeter_generator", "arachne" if p.get("wall_generator")=="arachne" else "classic")
    s("external_perimeters_first", 1 if p.get("wall_sequence")=="outer wall/inner wall" else 0)
    s("gcode_resolution", num(p.get("resolution", 0.0125)))
    s("slice_closing_radius", num(p.get("slice_closing_radius", 0.049)))
    s("dont_support_bridges", num(p.get("bridge_no_support", 0)))
    s("support_material", num(p.get("enable_support", 0)))
    s("support_material_threshold", num(p.get("support_threshold_angle", 0)))
    s("support_material_style", "snug")
    s("support_material_pattern", "rectilinear")
    s("support_material_interface_pattern", "auto")
    s("support_material_contact_distance", num(p.get("support_top_z_distance", 0.2)))
    s("support_material_bottom_contact_distance", num(p.get("support_bottom_z_distance", 0.2)))
    s("support_material_xy_spacing", num(p.get("support_object_xy_distance", 0.35)))
    s("support_material_angle", num(p.get("support_angle", 0)))
    s("support_material_spacing", num(p.get("support_base_pattern_spacing", 2.5)))
    s("support_material_interface_spacing", num(p.get("support_interface_spacing", 0.5)))
    s("support_material_interface_layers", num(p.get("support_interface_top_layers", 2)))
    s("support_material_bottom_interface_layers", num(p.get("support_interface_bottom_layers", 2)))
    s("raft_layers", num(p.get("raft_layers", 0)))
    s("raft_contact_distance", num(p.get("raft_contact_distance", 0.1)))
    s("raft_expansion", num(p.get("raft_expansion", 1.5)))
    s("raft_first_layer_density", str(p.get("raft_first_layer_density","90%")))
    s("raft_first_layer_expansion", num(p.get("raft_first_layer_expansion", 2)))
    s("gcode_label_objects", num(p.get("gcode_label_objects", 1)))
    return {k: val for k, val in v.items() if k in KEYS["print"]}

# ---------------- filament -> filament preset values -------------------------
def conv_filament(f, ftype):
    def g(k, d=None):
        return first(f.get(k, d))
    v = {}
    v["filament_type"] = ftype
    v["filament_vendor"] = g("filament_vendor", "Anycubic") or "Anycubic"
    v["filament_diameter"] = num(g("filament_diameter", 1.75))
    v["extrusion_multiplier"] = num(g("filament_flow_ratio", 1))
    v["filament_max_volumetric_speed"] = num(g("filament_max_volumetric_speed", 12))
    v["filament_density"] = num(g("filament_density", 1.24))
    v["filament_cost"] = num(g("filament_cost", 20))
    v["temperature"] = num(g("nozzle_temperature", 210))
    v["first_layer_temperature"] = num(g("nozzle_temperature_initial_layer", 215))
    v["bed_temperature"] = num(g("hot_plate_temp", 60))
    v["first_layer_bed_temperature"] = num(g("hot_plate_temp_initial_layer", 60))
    v["min_fan_speed"] = num(g("fan_min_speed", 100))
    v["max_fan_speed"] = num(g("fan_max_speed", 100))
    v["bridge_fan_speed"] = num(g("overhang_fan_speed", 100))
    v["disable_fan_first_layers"] = num(g("close_fan_the_first_x_layers", 1))
    v["fan_always_on"] = 1
    v["cooling"] = num(g("slow_down_for_layer_cooling", 1))
    v["slowdown_below_layer_time"] = num(g("slow_down_layer_time", 8))
    v["min_print_speed"] = num(g("slow_down_min_speed", 20))
    v["fan_below_layer_time"] = num(g("fan_cooling_layer_time", 100))
    v["full_fan_speed_layer"] = num(g("full_fan_speed_layer", 0))
    v["filament_soluble"] = num(g("filament_soluble", 0))
    v["filament_notes"] = ""
    v["filament_colour"] = "#DDDDDD"
    v["start_filament_gcode"] = "; filament start gcode"
    v["end_filament_gcode"] = "; filament end gcode"
    return {k: val for k, val in v.items() if k in KEYS["filament"]}

# ---------------- source loading helpers -------------------------------------
def load(path):
    return json.load(open(path))

def nozzle_of(name):
    m = re.search(r"(\d\.\d+)\s*nozzle", name)
    return m.group(1) if m else None

def select_files(subdir, sp):
    out = []
    for fp in glob.glob(os.path.join(SRC, subdir, "*.json")):
        b = os.path.basename(fp)
        if sp["src_name"] == "Anycubic Kobra S1" and "Kobra S1 Max" in b:
            continue
        if ("@" + sp["src_name"] + " ") in b or b.startswith(sp["src_name"] + " ") or b == sp["src_name"] + ".json":
            out.append(fp)
    return out

# ---------------- YAML emission (hand-rolled for exact style) ----------------
def y_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v)
    if s == "":
        return "''"
    if re.search(r"[:#\[\]{}&*!|>'\"%@`,]", s) or s.strip() != s or s.lower() in ("null","yes","no","true","false","~") or re.match(r"^[\d\.\-+]+$", s):
        return "'" + s.replace("'", "''") + "'"
    return s

def block_scalar(k, text, indent):
    pad = " " * indent
    out = [f"{pad}{k}: |-"]
    for ln in text.split("\n"):
        out.append(f"{pad}  {ln}")
    return "\n".join(out)

def emit_values(values, indent):
    pad = " " * indent
    lines = []
    for k, v in values.items():
        if isinstance(v, str) and "\n" in v:
            lines.append(block_scalar(k, v, indent))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            for item in v:
                lines.append(f"{pad}- {y_scalar(item)}")
        else:
            lines.append(f"{pad}{k}: {y_scalar(v)}")
    return "\n".join(lines)

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)

def png_to_svg(png_path, svg_path):
    """Wrap a PNG cover image in a valid SVG (Prusa wizard thumbnails must be SVG)."""
    if not os.path.exists(png_path):
        return None
    b64 = base64.b64encode(open(png_path, "rb").read()).decode()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
           'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1000 1000">'
           f'<image width="1000" height="1000" xlink:href="data:image/png;base64,{b64}"/>'
           '</svg>')
    os.makedirs(os.path.dirname(svg_path), exist_ok=True)
    open(svg_path, "w").write(svg)
    return os.path.basename(svg_path)

# ---------------- build one printer bundle -----------------------------------
def build(spkey):
    sp = PRINTERS[spkey]
    tok = sp["token"]      # space-free identifier for ids / base_model / conditions
    label = sp["label"]    # human-facing name used in preset names
    ver = "1.0.3"
    repo_root = os.path.join(BUILD, sp["vendor_id"])
    if os.path.exists(repo_root):
        shutil.rmtree(repo_root)
    vdir = os.path.join(repo_root, sp["vendor_id"], ver)
    os.makedirs(os.path.join(vdir, "assets"), exist_ok=True)

    machines = {}
    for fp in select_files("machine", sp):
        d = load(fp)
        if d.get("type") == "machine":
            nz = d.get("printer_variant") or nozzle_of(os.path.basename(fp))
            if nz in NOZZLES:
                machines[nz] = d
    base_m = machines.get("0.4") or next(iter(machines.values()))

    def copy_asset(src, dst):
        s = os.path.join(SRC, src)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(vdir, "assets", dst)); return dst
        return None
    bed_stl = copy_asset(f"{sp['src_name']}_buildplate_model.stl", f"{sp['vendor_id']}_bed.stl")
    bed_svg = copy_asset(f"{sp['src_name']}_buildplate_texture.svg", f"{sp['vendor_id']}.svg")
    # Thumbnail: raw PNG (PrusaSlicer's card renderer handles PNG; an SVG that just wraps a
    # raster does not render). The reference test fixture also uses a plain .png thumbnail.
    thumb = copy_asset(f"{sp['src_name']}_cover.png", f"{sp['vendor_id']}_thumbnail.png")

    # vendor.yaml
    vy = []
    vy.append("kind: vendor\nid: %s\nname: %s\nversion: %s\nfeatures:\n  printer:\n"
              "    supports_high_flow_nozzle:\n      default: false\n      user_editable: false\n"
              "    input_shaper:\n      default: true\n      user_editable: false\n"
              "    supports_025_nozzle:\n      default: true\n    supports_04_nozzle:\n      default: true\n"
              "    supports_06_nozzle:\n      default: true\n    supports_08_nozzle:\n      default: true\n"
              "    multi_extruder:\n      default: false\n  tool:\n    nozzle_diameter:\n      default: 0.4\n"
              "      user_editable: false\n  sheet:\n    cold:\n      default: false\n      user_editable: false"
              % (sp["vendor_id"], sp["repo_name"], ver))
    printer_doc = [
        "kind: printer", "technology: FFF", f"name: {sp['src_name']}", f"id: {tok}",
        "model:", f"  base_model: {tok}", f"  model: {tok}", "tool_count: 1",
        # HF nozzles left enabled: when merged into PrusaResearch the printer inherits
        # supports_high_flow_nozzle=true, so HF tool variants are offered. Disabling it
        # out from under an already-selected HF printer crashes PrusaSlicer on restart.
        "features:", "  input_shaper:", "    default: true", "visual:",
        f"  bed_model: {bed_stl}", f"  bed_texture: {bed_svg}", f"  thumbnail: {thumb}",
    ]
    vy.append("\n".join(printer_doc))
    for nz in NOZZLES:
        supp = "supports_%s_nozzle" % nz.replace("0.", "0")
        vy.append("\n".join([
            "kind: tool", "technology: FFF", f"id: '{nz}'", f"name: '{nz}'",
            f"condition: printer.{supp}", "features:", "  nozzle_diameter:", f"    default: {nz}"]))
    vy.append("\n".join(["kind: sheet", "id: pei_textured", "name: PEI Textured", "type: pei_textured"]))
    vy.append("\n".join([
        "kind: printer_config", f"id: {tok.lower()}", f"name: {sp['src_name']}",
        f"printer: {tok}", f"legacy_printer_model: [{tok}]", "tool_count: 1",
        "tools:", "- tool: '0.4'", "sheet: pei_textured"]))
    write(os.path.join(vdir, "vendor.yaml"), "\n---\n".join(vy) + "\n")

    def clean(nm):
        return re.sub(r"\s*@.*$", "", nm).strip()
    def leafname(nm, nz):
        return f"{clean(nm)} @{label} {nz}"

    default_print = {}
    for nz in NOZZLES:
        m = machines.get(nz)
        if m and m.get("default_print_profile"):
            default_print[nz] = leafname(m["default_print_profile"], nz)

    # preset-printer
    dp_id = "*default_print_%s*" % tok
    pp = []
    hdr = [f"id: '{dp_id}'", "kind: printer", "variants:"]
    for nz in NOZZLES:
        if nz in default_print:
            hdr += [f"- condition: tool.nozzle_diameter == {nz}", f"  id: {gid()}",
                    "  values:", f"    default_print: {default_print[nz]}"]
    pp.append("\n".join(hdr))
    mv = conv_machine(base_m, sp)
    doc2 = [f"id: {gid()}", "kind: printer", "inherits:", f"- '{dp_id}'", "variants:",
            f'- condition: printer.base_model == "{tok}"', f"  name: {sp['src_name']}",
            f"  id: {gid()}", "  values:"]
    pp.append("\n".join(doc2) + "\n" + emit_values(mv, 4))
    slug = tok.lower()
    write(os.path.join(vdir, f"preset-printer-{slug}.yaml"), "\n---\n".join(pp) + "\n")

    # preset-tool
    write(os.path.join(vdir, f"preset-tool-{slug}.yaml"),
          "\n".join(["kind: tool_print", f"id: common {tok}", "variants:",
                     f'- condition: printer.base_model == "{tok}"', "  name: no tool",
                     f"  id: {gid()}"]) + "\n")

    # preset-print
    procs = {nz: [] for nz in NOZZLES}
    for fp in select_files("process", sp):
        d = load(fp)
        if d.get("type") != "process":
            continue
        nz = nozzle_of(os.path.basename(fp))
        if nz in procs:
            procs[nz].append(d)
    ret = conv_retraction(base_m)
    common = ["kind: print", "id: '*common*'", "values:",
              f"  default_material: Anycubic PLA @{label}"]
    concrete = [f"id: {gid()}", "kind: print", "inherits:", "- '*common*'", "variants:",
                f'- condition: printer.base_model == "{tok}"', f"  id: {gid()}", "  values:"]
    concrete.append(emit_values(ret, 4))
    concrete.append("  variants:")
    for nz in NOZZLES:
        if not procs[nz]:
            continue
        m = machines.get(nz, base_m)
        nzvals = {}
        if m.get("max_layer_height"): nzvals["max_layer_height"] = num(first(m["max_layer_height"]))
        if m.get("min_layer_height"): nzvals["min_layer_height"] = num(first(m["min_layer_height"]))
        concrete.append(f"  - condition: tool.nozzle_diameter == {nz}")
        concrete.append(f"    id: {gid()}")
        concrete.append("    values:")
        if nzvals:
            concrete.append(emit_values(nzvals, 6))
        concrete.append("    variants:")
        for d in sorted(procs[nz], key=lambda x: x["name"]):
            vals = conv_process(d)
            concrete.append(f"    - name: {leafname(d['name'], nz)}")
            concrete.append(f"      id: {gid()}")
            concrete.append("      values:")
            concrete.append(emit_values(vals, 8))
    write(os.path.join(vdir, f"preset-print-{slug}.yaml"),
          "\n---\n".join(["\n".join(common), "\n".join(concrete)]) + "\n")

    # preset-filament
    fil_files = select_files("filament", sp)
    chosen = {}
    for prefix, ftype in FIL_TYPES.items():
        cands = [fp for fp in fil_files if os.path.basename(fp).startswith(prefix + " @")]
        if not cands:
            continue
        pref = [c for c in cands if "0.4 nozzle" in c] or cands
        chosen[prefix] = (pref[0], ftype)
    fdocs = []
    for ft in sorted(set(ft for _, ft in chosen.values())):
        fdocs.append("\n".join(["kind: filament", f"id: '*{ft}*'", "values: {}"]))
    for prefix, (fp, ftype) in chosen.items():
        d = load(fp)
        vals = conv_filament(d, ftype)
        name = f"{prefix} @{label}"
        doc = ["kind: filament", f"name: {name}", f'condition: printer.base_model == "{tok}"',
               f"id: {name}", "inherits:", f"- '*{ftype}*'", "values:"]
        fdocs.append("\n".join(doc) + "\n" + emit_values(vals, 2))
    write(os.path.join(vdir, f"preset-filament-{slug}.yaml"), "\n---\n".join(fdocs) + "\n")

    # per-version manifest.json
    entries = []
    for root, _, files in os.walk(vdir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, vdir).replace(os.sep, "/")
            if rel == "manifest.json":
                continue
            h = hashlib.sha256(open(full, "rb").read()).hexdigest()
            entries.append({"filename": rel, "filehash": h})
    entries.sort(key=lambda e: e["filename"])
    write(os.path.join(vdir, "manifest.json"), json.dumps(entries))

    # vendor_indices.zip + <Vendor>.idx
    idx = (f"min_slic3r_version = 3.0.0-alpha0\n"
           f"{ver} Re-enable HF nozzle; PNG thumbnail.\n"
           f"1.0.1 Space-free printer model ids so the printer appears in the wizard.\n"
           f"1.0.0 Initial release. Converted from AnycubicSlicerNext.\n")
    idx_path = os.path.join(repo_root, sp["vendor_id"] + ".idx")
    write(idx_path, idx)
    with zipfile.ZipFile(os.path.join(repo_root, "vendor_indices.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        z.write(idx_path, sp["vendor_id"] + ".idx")
    os.remove(idx_path)

    # top-level manifest.json
    top = {"name": sp["repo_name"], "description": sp["repo_name"], "visibility": "",
           "id": sp["repo_id"],
           "url": f"http://localhost:8000/v2/repos/{sp['repo_id']}",
           "index_url": f"http://localhost:8000/v2/repos/{sp['repo_id']}/vendor_indices.zip",
           "offline_archive_url": f"http://localhost:8000/v2/{sp['repo_id']}/{sp['zip_name']}",
           "version": 2}
    write(os.path.join(repo_root, "manifest.json"), json.dumps(top))

    # final offline zip
    os.makedirs(OUT, exist_ok=True)
    outzip = os.path.join(OUT, sp["zip_name"])
    if os.path.exists(outzip):
        os.remove(outzip)
    with zipfile.ZipFile(outzip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(repo_root):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, repo_root))
    return outzip, sum(len(procs[nz]) for nz in NOZZLES), len(chosen), len(machines)

if __name__ == "__main__":
    for k in ("s1", "x"):
        z, nproc, nfil, nmach = build(k)
        print(f"{k}: {os.path.relpath(z, ROOT)}  (machines={nmach} processes={nproc} filaments={nfil})")
    shutil.rmtree(BUILD, ignore_errors=True)
