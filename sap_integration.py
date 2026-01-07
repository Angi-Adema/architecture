# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements, Areas)
# - Builds model in SAP2000
# - Runs analysis (Dead self-weight, optional soil sweep)
# - Optional soil: stiffness-based base springs + constant overburden pressure (backend-fixed)
# - Exports model + results to <input>_results.xlsx

import importlib
import json
import os
import math
import re
from xml.parsers.expat import model
import pandas as pd
import comtypes.client as cc
import numpy as np
import ctypes
from collections import defaultdict

def _sap_get_name_list(res):
    """
    Normalize SAP2000 GetNameList() return into a Python list[str].

    Common shapes seen:
      1) (ret, count, names)
      2) (count, names, ret)   <-- YOUR SAP BUILD
      3) (count, names)
      4) names
    """
    if res is None:
        return []

    if not isinstance(res, (list, tuple)):
        return []

    # Convert to list for easier handling
    r = list(res)

    # --- 3-item shapes ---
    if len(r) == 3:
        a, b, c = r[0], r[1], r[2]

        # YOUR BUILD: (count, names, ret)
        if isinstance(a, (int, float)) and isinstance(b, (list, tuple)) and isinstance(c, (int, float)):
            return [str(x) for x in b]

        # Common: (ret, count, names)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and isinstance(c, (list, tuple)):
            return [str(x) for x in c]

        # Fallback: return the “most list-like” item
        for item in (a, b, c):
            if isinstance(item, (list, tuple)):
                return [str(x) for x in item]
        return []

    # --- 2-item shapes: (count, names) ---
    if len(r) == 2:
        a, b = r[0], r[1]
        if isinstance(a, (int, float)) and isinstance(b, (list, tuple)):
            return [str(x) for x in b]
        if isinstance(b, (int, float)) and isinstance(a, (list, tuple)):
            return [str(x) for x in a]
        return []

    # --- already a list/tuple of names ---
    return [str(x) for x in r]

def _filter_bad_sap_names(names):
    bad = {"global", "", "none"}
    out = []
    for n in names or []:
        s = str(n).strip()
        if s.lower() in bad:
            continue
        out.append(s)
    return out

def _assign_area_surface_pressure_by_uniform(
    model,
    area_name,
    load_pattern,
    pressure_Npm2,
    coord_sys="Global",
    replace=True
):
    """
    Assign a constant surface pressure to a shell/area object.
    Tries a few common SAP2000 COM signatures across versions.
    Returns True if any call returns ret == 0.

    IMPORTANT:
    - Uses _log_load_fail(...) and _log_load_ok(...) so _log_load_summary() is meaningful.
    - Still uses _log_load_fail_once(...) to keep console output short.
    """
    area = model.AreaObj
    name = str(area_name)
    patt = str(load_pattern)
    csys = str(coord_sys)
    repl = bool(replace)

    try:
        p = float(pressure_Npm2)
    except Exception as e:
        _log_load_fail("PressureCast", area_name, e)
        _log_load_fail_once("Pressure cast failed:", "area=", area_name, "pressure=", pressure_Npm2, "err=", repr(e))
        return False

    DIR_CANDIDATES = (1,)

    # ---- 1) SetLoadSurfacePressure(name, pattern, dirEnum, value, coordSys, replace)
    for d in DIR_CANDIDATES:
        try:
            ret = area.SetLoadSurfacePressure(name, patt, int(d), p, csys, repl)
            if ret == 0:
                _log_load_ok("SurfacePressure(sigA)")
                _log("OB ok:", "area=", area_name, "via=SurfacePressure", "dir=", d, "p=", p)
                return True
            else:
                # SAP returned a nonzero ret code (not an exception)
                _log_load_fail("SurfacePressure(sigA_ret)", area_name, f"ret={ret}")
        except Exception as e:
            _log_load_fail("SurfacePressure(sigA_exc)", area_name, e)
            _log_load_fail_once("SurfacePressure(sigA) failed:", "area=", area_name, "dir=", d, "err=", repr(e))

    # ---- 2) SetLoadSurfacePressure(name, pattern, "Projected", value, coordSys, replace)
    try:
        ret = area.SetLoadSurfacePressure(name, patt, "Projected", p, csys, repl)
        if ret == 0:
            _log_load_ok("SurfacePressure(sigB)")
            _log("OB ok:", "area=", area_name, "via=SurfacePressure", "type=Projected", "p=", p)
            return True
        else:
            _log_load_fail("SurfacePressure(sigB_ret)", area_name, f"ret={ret}")
    except Exception as e:
        _log_load_fail("SurfacePressure(sigB_exc)", area_name, e)
        _log_load_fail_once("SurfacePressure(sigB) failed:", "area=", area_name, "err=", repr(e))

    # ---- 3) SetLoadUniform(name, pattern, value, dirEnum, replace)
    for d in DIR_CANDIDATES:
        try:
            ret = area.SetLoadUniform(name, patt, p, int(d), repl)
            if ret == 0:
                _log_load_ok("Uniform(sigA)")
                _log("OB ok:", "area=", area_name, "via=Uniform", "dir=", d, "p=", p)
                return True
            else:
                _log_load_fail("Uniform(sigA_ret)", area_name, f"ret={ret}")
        except Exception as e:
            _log_load_fail("Uniform(sigA_exc)", area_name, e)
            _log_load_fail_once("Uniform(sigA) failed:", "area=", area_name, "dir=", d, "err=", repr(e))

    # ---- 4) SetLoadUniform(name, pattern, value, coordSys, replace)
    try:
        ret = area.SetLoadUniform(name, patt, p, csys, repl)
        if ret == 0:
            _log_load_ok("Uniform(sigB)")
            _log("OB ok:", "area=", area_name, "via=Uniform", "csys=", csys, "p=", p)
            return True
        else:
            _log_load_fail("Uniform(sigB_ret)", area_name, f"ret={ret}")
    except Exception as e:
        _log_load_fail("Uniform(sigB_exc)", area_name, e)
        _log_load_fail_once("Uniform(sigB) failed:", "area=", area_name, "err=", repr(e))

    return False



# Ensure xlsxwriter is available for pandas' ExcelWriter(engine="xlsxwriter")
def _ensure_xlsxwriter():
    try:
        import xlsxwriter  # noqa: F401
    except ImportError:
        raise RuntimeError("Please `pip install xlsxwriter` to write results.")


def _ensure_openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise RuntimeError("Please `pip install openpyxl` to read .xlsx files.")


# --- DEBUG SWITCH ---
DEBUG = True
DEBUG_COM_SHAPES = False

# ---- Soil constants (backend-fixed) ----
OVERBURDEN_PRESSURE_NPM2 = 18000.0  # N/m^2 (Pa) constant (backend)
SOIL_LOAD_PATTERN = "SOIL_OB"       # load pattern name
SOIL_CASE_NAME = "SOIL_CASE"


def _log(*a, sep=" ", end="\n"):
    """Lightweight debug logger - only prints when DEBUG is True."""
    if DEBUG:
        try:
            print("[DEBUG]", *a, sep=sep, end=end, flush=True)
        except Exception:
            pass

# --- Limit spammy per-area load errors ---
_FAIL_COUNT = 0

def _reset_load_logs():
    """
    Clear signature counters so new runs start fresh.
    """
    _LOAD_FAIL.clear()
    _LOAD_FAIL_SAMPLES.clear()
    _LOAD_OK.clear()

def _reset_fail_counter():
    global _FAIL_COUNT
    _FAIL_COUNT = 0

def _log_load_fail_once(*msg):
    """
    Log only the first few per-area load failures to keep console readable.
    """
    global _FAIL_COUNT
    _FAIL_COUNT += 1
    if _FAIL_COUNT <= 5:
        _log(*msg)
    elif _FAIL_COUNT == 6:
        _log("[DEBUG] (suppressing further area load errors...)")

_LOAD_FAIL = defaultdict(int)
_LOAD_FAIL_SAMPLES = defaultdict(list)
_LOAD_OK = defaultdict(int)

def _log_load_fail(sig, area_name, err):
    _LOAD_FAIL[sig] += 1
    # store up to 3 samples per signature
    if len(_LOAD_FAIL_SAMPLES[sig]) < 3:
        _LOAD_FAIL_SAMPLES[sig].append((str(area_name), repr(err)))

def _log_load_ok(sig):
    _LOAD_OK[sig] += 1

def _log_load_summary():
    # print once at end of overburden assignment
    for sig in sorted(set(list(_LOAD_FAIL.keys()) + list(_LOAD_OK.keys()))):
        ok = _LOAD_OK.get(sig, 0)
        bad = _LOAD_FAIL.get(sig, 0)
        if ok or bad:
            _log(f"[LOADSIG] {sig}: ok={ok} fail={bad}")
            for area_name, err in _LOAD_FAIL_SAMPLES.get(sig, []):
                _log(f"[LOADSIG] sample fail {sig}: area={area_name} err={err}")

# ---- Paths for v20 (adjust if installed elsewhere) ----
DIR_CANDIDATES = [
    r"C:\Program Files\SAP2000 20",
    r"C:\Program Files\Computers and Structures\SAP2000 20",
]

def _as_seq(x, n):
    """Coerce COM return 'x' to a sequence of length n (pad/trim safely)."""
    if n is None:
        n = 0
    n = int(n)

    # Try to treat x as a sequence
    try:
        seq = list(x)
    except Exception:
        return [x] * n

    # Trim if too long
    if len(seq) > n:
        return seq[:n]

    # Pad if too short
    if len(seq) < n:
        pad_val = seq[-1] if seq else None
        seq.extend([pad_val] * (n - len(seq)))

    return seq


def _select_case(model, case):
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
        model.Results.Setup.SetCaseSelectedForOutput(case)
        model.Results.Setup.SetOptionMode(0)  # 0 = use current selection, if available
    except Exception:
        pass


def _find_sap_paths():
    exe_path, tlb_path = None, None
    for d in DIR_CANDIDATES:
        if not os.path.isdir(d):
            continue
        p_exe = os.path.join(d, "SAP2000.exe")

        # Look for versioned TLBs
        tlb_candidates = [
            os.path.join(d, "SAP2000v20.tlb"),
            os.path.join(d, "SAP2000v19.tlb"),
            os.path.join(d, "SAP2000v1.tlb"),
        ]

        if os.path.isfile(p_exe) and exe_path is None:
            exe_path = p_exe
        for p_tlb in tlb_candidates:
            if os.path.isfile(p_tlb) and tlb_path is None:
                tlb_path = p_tlb
                break
    return exe_path, tlb_path


def _start_sap2000_v20(visible=True):
    exe_path, tlb_path = _find_sap_paths()

    # Optional: load type library and import its generated module as s2k
    s2k = None
    try:
        if tlb_path:
            cc.GetModule(tlb_path)
            module_name = os.path.splitext(os.path.basename(tlb_path))[0]
            s2k = importlib.import_module(f"comtypes.gen.{module_name}")
    except Exception:
        s2k = None  # not critical

    sap = None
    # Preferred: direct SapObject ProgID
    try:
        sap = cc.CreateObject("CSI.SAP2000.API.SapObject")
    except Exception:
        # Helpers (some installs register v1, some v20)
        helper = None
        for progid in ("SAP2000v1.Helper", "SAP2000v20.Helper"):
            try:
                helper = cc.CreateObject(progid)
                break
            except Exception:
                continue
        if helper is not None:
            try:
                sap = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
            except Exception:
                sap = None

        # Attach to a running instance
        if sap is None:
            try:
                sap = cc.GetActiveObject("CSI.SAP2000.API.SapObject")
            except Exception:
                # Last resort: launch then attach
                if not exe_path:
                    raise RuntimeError("Could not locate SAP2000.exe. Update DIR_CANDIDATES.")
                import time as _time
                import os as _os
                _os.startfile(exe_path)
                _log("Launched EXE, attaching…")

                for _ in range(20):
                    _time.sleep(1)
                    try:
                        sap = cc.GetActiveObject("CSI.SAP2000.API.SapObject")
                        _log("Attached to SAP2000 instance.")
                        break
                    except Exception:
                        pass

                if sap is None:
                    _log("Timed out waiting for SAP2000 to register COM object.")
                    raise RuntimeError("SAP2000 did not register in time after launch.")

    # Fail fast if we still didn't get an object
    if sap is None:
        raise RuntimeError("Could not obtain CSI.SAP2000.API.SapObject via CreateObject, Helper, or GetActiveObject.")

    try:
        _log("SAP2000 version:", sap.GetVersion())  # returns string on many v20 builds
    except Exception:
        pass

    # ---- common startup (run once) ----
    try:
        sap.ApplicationStart()
    except Exception:
        pass
    try:
        sap.SetAsActiveObject()
    except Exception:
        pass

    if visible:
        try:
            sap.Visible()  # v20: no args
        except Exception:
            pass

    model = sap.SapModel
    model.InitializeNewModel()

    # Try plain NewBlank(), fall back to metric-flag variant on older builds
    try:
        model.File.NewBlank()
    except Exception:
        try:
            model.File.NewBlank(True)  # some builds accept a 'metric' flag
        except Exception as e:
            _log(f"NewBlank failed with and without metric flag: {e}")
            raise

    # ---- Set units (guarded). Prefer enum; fallback to numeric 8 for v20 (N–m–C). ----
    try:
        if hasattr(model, "SetPresentUnits"):
            if s2k and hasattr(s2k, "eUnits_N_m_C"):
                model.SetPresentUnits(s2k.eUnits_N_m_C)
            else:
                # NOTE: In SAP2000 v20, numeric code 8 = N–m–C on many builds.
                model.SetPresentUnits(8)
        else:
            _log("SetPresentUnits not available; leaving default units.")
    except Exception:
        _log("Unable to set present units; leaving default units.")

    # Optional: sanity check
    try:
        ret, units = model.GetPresentUnits()  # <- capital G
        _log(f"Present units code: {units}")
    except Exception:
        pass

    return sap, model


def _read_nodes_sheet(input_xlsx):
    df = pd.read_excel(input_xlsx, sheet_name="Nodes", header=None)

    # If first row looks like headers, drop it
    first_row = df.iloc[0].astype(str).str.strip().str.lower().tolist()
    if ("x" in first_row and "y" in first_row and "z" in first_row) or ("name" in first_row):
        df = df.iloc[1:].reset_index(drop=True)

    # Assume Name, X, Y, Z are first 4 columns
    name_col, x_col, y_col, z_col = 0, 1, 2, 3

    out = pd.DataFrame({
        "Name": df.iloc[:, name_col].astype(str).str.strip(),
        "X": df.iloc[:, x_col].astype(float),
        "Y": df.iloc[:, y_col].astype(float),
        "Z": df.iloc[:, z_col].astype(float),
    })

    out = out[out["Name"].notna() & (out["Name"] != "")]
    return out


def _read_elements_sheet(xlsx_path):
    """
    Reads 'Elements' sheet. Supports either:
      - no header row
      - or a header row like: Frame, I, J, Section, Material

    If the sheet is empty (common for shell-only models), returns an empty DF with proper columns.
    Also ignores "INTENTIONALLY BLANK ..." rows if present.
    """
    df = pd.read_excel(xlsx_path, sheet_name="Elements", header=None)

    if df is None or df.empty:
        return pd.DataFrame(columns=["Frame", "I", "J", "Section", "Material"])

    # Pad to 5 cols
    for c in range(df.shape[1], 5):
        df[c] = None

    # Detect and drop header-like first row
    first = df.iloc[0].astype(str).str.strip().str.lower().tolist()
    header_hits = {"frame", "i", "j", "section", "material"}
    if any(v in header_hits for v in first):
        df = df.iloc[1:].reset_index(drop=True)

    df = df.rename(columns={0: "Frame", 1: "I", 2: "J", 3: "Section", 4: "Material"})[
        ["Frame", "I", "J", "Section", "Material"]
    ]

    df = df[~df["Frame"].astype(str).str.contains("INTENTIONALLY BLANK", case=False, na=False)].copy()
    df = df[df["Frame"].notna() & (df["Frame"].astype(str).str.strip() != "")].copy()

    def _canon_point(v):
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.lower() == "nan":
            return None
        try:
            f = float(s)
            if np.isnan(f):
                return None
            if f.is_integer():
                return str(int(f))
            return str(f)
        except Exception:
            return s

    df["I"] = df["I"].apply(_canon_point)
    df["J"] = df["J"].apply(_canon_point)

    return df.reset_index(drop=True)


def _read_areas_sheet(xlsx_path):
    """
    Reads 'Areas' sheet.
    Expected rows (no header in your writer):
        AreaName, P1, P2, P3, P4, Section, Material

    But we also tolerate a header row like:
        Area, P1, P2, P3, P4, Section, Material

    Returns DataFrame columns:
        Area, P1, P2, P3, P4, Section, Material
    """
    import pandas as pd
    import numpy as np

    try:
        df = pd.read_excel(
            xlsx_path,
            sheet_name="Areas",
            header=None,
            engine="openpyxl"
        )
    except Exception:
        return pd.DataFrame(columns=["Area", "P1", "P2", "P3", "P4", "Section", "Material"])

    if df is None or df.empty:
        return pd.DataFrame(columns=["Area", "P1", "P2", "P3", "P4", "Section", "Material"])

    # Drop fully empty rows (Excel often has trailing blank rows)
    df = df.dropna(how="all").reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=["Area", "P1", "P2", "P3", "P4", "Section", "Material"])

    # Ensure at least 7 columns (pad with None)
    while df.shape[1] < 7:
        df[df.shape[1]] = None

    # Only keep first 7 columns (ignore any extras)
    df = df.iloc[:, :7]

    # Detect header row ONLY if it matches strongly
    def _norm(s):
        return str(s).strip().lower()

    first = [_norm(v) for v in df.iloc[0].tolist()]
    header_tokens = {"area", "areaname", "p1", "p2", "p3", "p4", "section", "material"}

    # Count matches instead of "any" to avoid false positives
    hits = sum(1 for v in first if v in header_tokens)
    if hits >= 3:  # require at least 3 header-like tokens
        df = df.iloc[1:].reset_index(drop=True)

    # Apply column names
    df.columns = ["Area", "P1", "P2", "P3", "P4", "Section", "Material"]

    # Drop rows with missing/blank Area
    df["Area"] = df["Area"].astype(str).str.strip()
    df = df[df["Area"].notna() & (df["Area"] != "") & (df["Area"].str.lower() != "nan")].copy()
    if df.empty:
        return pd.DataFrame(columns=["Area", "P1", "P2", "P3", "P4", "Section", "Material"])

    # Canonicalize point IDs: 1 / 1.0 / "1.0" -> "1"
    def _canon_point(v):
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.lower() == "nan":
            return None
        try:
            f = float(s)
            if np.isnan(f):
                return None
            if f.is_integer():
                return str(int(f))
            # If you ever truly have non-integer point IDs, keep them
            return str(f)
        except Exception:
            return s

    for col in ["P1", "P2", "P3", "P4"]:
        df[col] = df[col].apply(_canon_point)

    # Normalize Section/Material: keep None if blank
    for col in ["Section", "Material"]:
        df[col] = df[col].apply(lambda v: None if v is None or str(v).strip() in ("", "nan", "None") else str(v).strip())

    # Drop any area rows missing required connectivity (P1..P3 required; P4 optional for tri)
    df = df[df["P1"].notna() & df["P2"].notna() & df["P3"].notna()].copy()

    return df.reset_index(drop=True)


def _ensure_default_section(model, section="RECT_300x500", material="CONC40",
                            dims=(0.30, 0.50), mat_type=2):
    """Ensure a simple rectangular frame section/material exists (mat_type: 1=Steel, 2=Concrete)."""
    try:
        model.PropMaterial.SetMaterial(material, mat_type)
    except Exception:
        pass
    b, h = dims
    try:
        model.PropFrame.SetRectangle(section, material, b, h)
    except Exception:
        pass


def _ensure_material_defined(model, spec):
    """
    Ensure a material exists and has basic properties.
    spec keys: name, type ('Concrete'|'Steel'), region ('User'|...), E, nu, alpha, gamma (N/m^3)
    """
    if not spec:
        return None
    name = str(spec.get("name", "CONC40"))
    mtype = str(spec.get("type", "Concrete")).strip().lower()
    region = str(spec.get("region", "User"))
    mat_code = 2 if mtype == "concrete" else 1

    try:
        model.PropMaterial.SetMaterial(name, mat_code)
    except Exception:
        try:
            model.PropMaterial.SetMaterial_1(name, region, "", "")
        except Exception:
            pass

    E_pa = float(spec.get("E", 30e9))  # Pa = N/m^2 for N-m units
    nu = float(spec.get("nu", 0.2))
    alpha = float(spec.get("alpha", 1.0e-5))

    try:
        model.PropMaterial.SetMPIsotropic(name, E_pa, nu, alpha)
    except Exception:
        try:
            model.PropMaterial.SetMPIsotropic_1(name, E_pa, nu, alpha)
        except Exception:
            pass

    gamma_Npm3 = float(spec.get("gamma", 24000.0))  # N/m^3
    gamma_kNpm3 = gamma_Npm3 / 1000.0
    mass_kN_s2_m4 = gamma_kNpm3 / 9.80665
    try:
        model.PropMaterial.SetWeightAndMass(name, gamma_kNpm3, mass_kN_s2_m4)
    except Exception:
        pass

    return name


def _mat_code_from_name(name: str, default: int = 2) -> int:
    """
    Guess SAP material type code from a material name.
    Returns 1=Steel, 2=Concrete (default=2).
    """
    n = (name or "").strip().lower()
    if any(k in n for k in ("steel", "stl", "a36", "a992", "fy", "s ")):
        return 1
    if any(k in n for k in ("conc", "concrete", "c", "fc'", "fc ")):
        return 2
    return default


def _build_model_from_excel(model, nodes_df, elems_df,
                            default_section=("RECT_300x500", "CONC40", (0.30, 0.50))):
    """
    Create points and frames in SAP2000 from the provided dataframes.

    nodes_df columns: Name, X, Y, Z
    elems_df columns: Frame, I, J, Section (opt), Material (opt)
    """
    sec_name, mat_name, dims = default_section
    _ensure_default_section(model, sec_name, mat_name, dims)

    def _point_exists(pt_name: str) -> bool:
        try:
            model.PointObj.GetCoordCartesian(pt_name, "Global")
            return True
        except Exception:
            try:
                model.PointObj.GetCoordCartesian(pt_name)
                return True
            except Exception:
                return False

    # Points
    for _, r in nodes_df.iterrows():
        name, x, y, z = r["Name"], r["X"], r["Y"], r["Z"]
        try:
            model.PointObj.AddCartesian(float(x), float(y), float(z), str(name))
        except Exception:
            pass

    # Frames
    b, h = map(float, dims)
    for _, r in elems_df.iterrows():
        fname = str(r["Frame"]).strip()
        i_pt = str(r["I"]).strip()
        j_pt = str(r["J"]).strip()

        raw_sec = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        raw_mat = str(r["Material"]) if pd.notna(r["Material"]) else mat_name
        sec = (raw_sec or "").strip() or sec_name
        mat = (raw_mat or "").strip() or mat_name

        if not fname:
            raise RuntimeError(f"Elements sheet has a frame with empty name (I={i_pt}, J={j_pt}).")
        if i_pt == j_pt:
            raise RuntimeError(f"Frame '{fname}' has identical I and J points: {i_pt}.")

        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, _mat_code_from_name(mat))
            except Exception:
                try:
                    model.PropMaterial.SetMaterial_1(mat, "User", "", "")
                except Exception:
                    pass

        try:
            model.PropFrame.SetRectangle(sec, mat, b, h)
        except Exception:
            pass

        try:
            model.FrameObj.Delete(fname)
        except Exception:
            pass

        if not _point_exists(i_pt) or not _point_exists(j_pt):
            raise RuntimeError(f"Point '{i_pt}' or '{j_pt}' not found before creating frame '{fname}'.")

        try:
            model.FrameObj.AddByPoint(i_pt, j_pt, fname, sec, "Global")
        except Exception as e:
            raise RuntimeError(f"Failed to create frame {fname} ({i_pt}->{j_pt}): {e}")

        try:
            model.FrameObj.SetProperty(fname, sec)
        except Exception:
            try:
                model.FrameObj.SetSection(fname, sec)
            except Exception:
                pass

def _build_areas_from_excel(model, areas_df, default_section=("SHELL_200", "CONC40", 0.20)):
    sec_name, mat_name, thick_m = default_section
    area = model.AreaObj

    def _ensure_material(mat: str):
        try:
            # 2 is typically concrete in CSI material type enums (varies by API build)
            model.PropMaterial.SetMaterial(mat, 2)
        except Exception as e:
            raise RuntimeError(f"Failed to define material '{mat}': {repr(e)}")

    def _ensure_shell_prop(sec: str, mat: str, t: float):
        """
        SAP2000 COM builds differ in PropArea.SetShell signature.
        Some builds expect arg2 = ShellType (int), and thickness may not be 5th.
        We'll brute-force the common variants.
        """
        last = None
        shell_types = [4]

        def sig_mismatch(e: Exception) -> bool:
            return isinstance(e, (TypeError, ValueError, ctypes.ArgumentError))

        def try_call(fn_name: str, args):
            nonlocal last
            fn = getattr(model.PropArea, fn_name, None)
            if fn is None:
                return False
            try:
                fn(*args)
                _log("[DEBUG] ShellProp OK via", fn_name, "args=", args)
                return True
            except Exception as e:
                if sig_mismatch(e):
                    last = (fn_name, args, e)
                    return False
                # Real failure (not signature): surface it immediately
                raise RuntimeError(f"{fn_name} failed (not signature): args={args} err={repr(e)}")

        # We will try both SetShell and SetShell_1
        fns = ["SetShell", "SetShell_1"]

        # ---------- A) Variants where arg2 is material (older/newer simple builds) ----------
        for fn in fns:
            candidates = [
                (sec, mat, 0.0, float(t), 0, "", ""),   # (Name, MatProp, MatAng, Thick, Color, Notes, GUID)
                (sec, mat, 0.0, float(t)),              # (Name, MatProp, MatAng, Thick)
            ]
            for args in candidates:
                if try_call(fn, args):
                    return

        # ---------- B) Variants where arg2 is ShellType (int) ----------
        for fn in fns:
            for st in shell_types:
                candidates = [
                    # Most likely for your build (arg4 is Notes/string)
                    # (Name, ShellType, MatProp, Notes, MatAng, Thickness, Color, GUID)
                    (sec, int(st), mat, "", 0.0, float(t), 0, ""),

                    # Variant where GUID may be required as a string, but you also need a Notes2 slot:
                    # (Name, ShellType, MatProp, Notes, MatAng, Thickness, Color, Notes2, GUID)
                    (sec, int(st), mat, "", 0.0, float(t), 0, "", ""),

                    # 9-arg style where arg4 is Notes (string) and arg7 must be float
                    (sec, int(st), mat, "", 0.0, float(t), float(t), 0, ""),  # arg6 & arg7 both floats
                    (sec, int(st), mat, "", 0.0, float(t), 0.0,     0, ""),  # arg7 float, maybe "extra thickness" = 0
                    (sec, int(st), mat, "", 0.0, 0.0,     float(t), 0, ""),  # thickness might actually be arg7
                ]

                for args in candidates:
                    if try_call(fn, args):
                        return

        fn, args, err = last if last else ("SetShell/SetShell_1", None, "Unknown")
        raise RuntimeError(
            f"Failed to define shell property sec='{sec}' mat='{mat}' t={t}. "
            f"Last tried: {fn}{args} err={repr(err)}"
        )

    # Ensure the default baseline exists
    _ensure_material(mat_name)
    _ensure_shell_prop(sec_name, mat_name, float(thick_m))

    _log("[DEBUG] Shell property ensured:", sec_name, mat_name, thick_m)

    for i, (_, r) in enumerate(areas_df.iterrows()):
        name = str(r["Area"]).strip()
        p1 = str(r["P1"]).strip()
        p2 = str(r["P2"]).strip()
        p3 = str(r["P3"]).strip()
        p4 = r["P4"]

        sec = str(r["Section"]).strip() if pd.notna(r["Section"]) else sec_name
        mat = str(r["Material"]).strip() if pd.notna(r["Material"]) else mat_name

        # If the row requests a material, ensure it exists
        if pd.notna(r["Material"]):
            _ensure_material(mat)

        # Ensure the requested shell property exists
        _ensure_shell_prop(sec, mat, float(thick_m))

        # Points
        if p4 is None or (isinstance(p4, float) and pd.isna(p4)) or str(p4).strip() == "":
            point_names = [p1, p2, p3]
        else:
            point_names = [p1, p2, p3, str(p4).strip()]

        n_pts = len(point_names)

        created_name = name
        created = False
        last_err = None

        for meth in ("AddByPoint", "AddByPoint_1", "AddByPoint_2"):
            m = getattr(area, meth, None)
            if m is None:
                continue
            try:
                ret = m(n_pts, point_names, created_name, sec, "Global")
                created = True
                break
            except TypeError:
                try:
                    ret = m(n_pts, point_names, created_name)
                    created = True
                    break
                except Exception as e:
                    last_err = e
            except Exception as e:
                last_err = e

        if not created:
            raise RuntimeError(
                f"Failed to create area '{name}' with points {point_names}. Last err={repr(last_err)}"
            )

        # CRITICAL: Assign the property to the area (do not swallow)
        try:
            area.SetProperty(created_name, sec)
        except Exception as e:
            raise RuntimeError(
                f"Area '{created_name}' created but SetProperty failed for sec='{sec}'. err={repr(e)}"
            )
        
        if i < 5:
            try:
                prop = area.GetProperty(str(created_name))
                _log("[DEBUG] Area property confirmed:", created_name, repr(prop))
            except Exception as e:
                _log("[DEBUG] AreaObj.GetProperty FAILED:", created_name, repr(e))


def _fix_base_nodes(model, nodes_df, tol=1e-3, fix=(1, 1, 1, 1, 1, 1)):
    """
    Fix nodes at the minimum Z (within tol).
    fix: tuple/list of 6 flags (UX, UY, UZ, RX, RY, RZ).
    """
    zmin = float(nodes_df["Z"].min())
    base = nodes_df.loc[(nodes_df["Z"] - zmin).abs() <= tol, "Name"].astype(str).tolist()
    restr = list(fix)
    for n in base:
        model.PointObj.SetRestraint(n, restr)


def _add_default_self_weight(model, pattern="Dead", mult=1.0):
    """Create a Dead load pattern with self-weight multiplier."""
    try:
        model.LoadPatterns.Add(pattern, 1, mult)  # 1 = Dead
    except Exception:
        pass


def _run_analysis(model):
    try:
        ret = model.Analyze.RunAnalysis()
        _log(f"RunAnalysis ret={ret}")

        # 🔴 CRITICAL: force result cases to be selectable via API
        try:
            model.Results.Setup.DeselectAllCasesAndCombosForOutput()

            # Select ONLY the cases you will query
            model.Results.Setup.SetCaseSelectedForOutput("Dead")
            model.Results.Setup.SetCaseSelectedForOutput("SOIL_CASE")

            res = model.Results.Setup.GetCaseSelectedForOutput()
            _log("[DEBUG] Cases selected for output:", repr(res))
        except Exception as e:
            _log("[DEBUG] Output case selection failed:", repr(e))

        return ret
    except Exception as e:
        _log(f"RunAnalysis raised: {e}")
        raise

def _case_has_any_joint_displ_output(model, case_name, vertical_axis="Z"):
    """
    Returns True if the given case produced joint displacement output for at least
    one of several probe joints (vertex + rim + mid), to reduce false negatives.
    """
    try:
        joints = list(_iter_all_point_coords(model))  # (name,x,y,z)
        if not joints:
            _log("[DEBUG] JointDispl check: no joints found")
            return False

        ax = (vertical_axis or "Z").upper()
        axis_index = 2 if ax == "Z" else (1 if ax == "Y" else 0)

        # ---- Build probe joints ----
        # Vertex (highest along axis)
        vtx = max(joints, key=lambda t: t[1 + axis_index])[0]

        # Rim candidate: lowest along axis
        min_elev = min(t[1 + axis_index] for t in joints)
        rim = [t for t in joints if abs(t[1 + axis_index] - min_elev) <= 1e-6]

        # If we have rim points, pick the farthest from rim centroid (likely a corner)
        if rim:
            cx = sum(t[1] for t in rim) / len(rim)
            cy = sum(t[2] for t in rim) / len(rim)
            rim_pick = max(rim, key=lambda t: (t[1] - cx) ** 2 + (t[2] - cy) ** 2)[0]
        else:
            # fallback: just use the lowest point
            rim_pick = min(joints, key=lambda t: t[1 + axis_index])[0]

        # Mid-elevation point (median along axis)
        joints_sorted = sorted(joints, key=lambda t: t[1 + axis_index])
        mid_pick = joints_sorted[len(joints_sorted) // 2][0]

        # De-dupe while preserving order
        probes = []
        for nm in (vtx, rim_pick, mid_pick):
            s = str(nm)
            if s not in probes:
                probes.append(s)

        _select_case(model, case_name)

        def _get_nres_for_joint(joint_name):
            try:
                out = model.Results.JointDispl(str(joint_name), 0)
            except Exception:
                out = model.Results.JointDispl(str(joint_name), 0, case_name)

            ret, n_header, base = _sap_results_header(out)
            if base is None:
                return 0

            obj_raw = out[base + 0] if (len(out) > base + 0) else None
            try:
                n_actual = len(list(obj_raw)) if isinstance(obj_raw, (list, tuple)) else 0
            except Exception:
                n_actual = 0

            return int(n_actual or (n_header or 0))

        # ---- Probe ----
        for j in probes:
            nres = _get_nres_for_joint(j)
            _log("[DEBUG] JointDispl probe:", "case=", case_name, "joint=", j, "nres=", nres)
            if nres > 0:
                return True

        _log("[DEBUG] JointDispl check:", "case=", case_name, "no output on probes=", probes)
        return False

    except Exception as e:
        _log("[DEBUG] JointDispl check failed:", "case=", case_name, "err=", repr(e))
        return False
    
def _sap_results_header(out):
    """
    Normalize SAP Results return shapes.
    Returns: (ret, nres, base)
      - ret: SAP return code (0=OK)
      - nres: number of results
      - base: index where Obj array starts (so Obj is out[base])
    Supports both:
      A) (ret, nres, Obj, Elm, ...)
      B) (nres, Obj, Elm, ..., ret)
    """
    if not isinstance(out, (list, tuple)):
        return (None, 0, None)

    L = len(out)
    if L < 3:
        return (None, 0, None)

    def _as_int(x):
        if isinstance(x, (list, tuple)):
            x = x[0] if len(x) else 0
        try:
            return int(x)
        except Exception:
            return None

    first = _as_int(out[0])
    second = _as_int(out[1])
    last = _as_int(out[-1])

    # Shape A: (ret, nres, Obj, ...)
    if first is not None and first in (0, 1, 2) and second is not None and second >= 0:
        return (first, second, 2)

    # Shape B: (nres, Obj, ..., ret)
    if last is not None and last in (0, 1, 2) and first is not None and first >= 0:
        return (last, first, 1)

    # Fallback: assume classic
    if first is not None and second is not None:
        return (first, second, 2)

    return (None, 0, None)


def _collect_joint_displacements_per_node(model, node_names, case="Dead"):
    rows = []
    _select_case(model, case)

    # de-dupe node list (prevents accidental repeats)
    seen = set()
    node_names = [n for n in node_names if not (str(n) in seen or seen.add(str(n)))]

    def _call_jointdispl(nm):
        # ItemTypeElm = 0 (ObjectElm) is correct for a single joint name
        try:
            return model.Results.JointDispl(str(nm), 0)
        except Exception:
            return model.Results.JointDispl(str(nm), 0, str(case))

    for nname in node_names:
        try:
            out = _call_jointdispl(nname)
        except Exception:
            continue

        if not isinstance(out, (list, tuple)) or len(out) < 10:
            continue

        ret, n_header, base = _sap_results_header(out)
        if base is None:
            continue

        # --- IMPORTANT: infer n from actual array length, not header ---
        # Obj should be the first array at out[base+0]
        obj_raw = out[base + 0] if (len(out) > base + 0) else None
        try:
            n_actual = len(list(obj_raw)) if isinstance(obj_raw, (list, tuple)) else 0
        except Exception:
            n_actual = 0

        # pick the most trustworthy n
        n = int(n_actual or (n_header or 0))
        if n <= 0:
            continue

        # Layout starting at base:
        Obj      = _as_seq(out[base + 0], n)
        Elm      = _as_seq(out[base + 1], n) if len(out) > base + 1 else [None] * n
        LoadCase = _as_seq(out[base + 2], n) if len(out) > base + 2 else [None] * n
        StepType = _as_seq(out[base + 3], n) if len(out) > base + 3 else [None] * n
        StepNum  = _as_seq(out[base + 4], n) if len(out) > base + 4 else [None] * n

        U1 = _as_seq(out[base + 5],  n) if len(out) > base + 5 else [None] * n
        U2 = _as_seq(out[base + 6],  n) if len(out) > base + 6 else [None] * n
        U3 = _as_seq(out[base + 7],  n) if len(out) > base + 7 else [None] * n
        R1 = _as_seq(out[base + 8],  n) if len(out) > base + 8 else [None] * n
        R2 = _as_seq(out[base + 9],  n) if len(out) > base + 9 else [None] * n
        R3 = _as_seq(out[base + 10], n) if len(out) > base + 10 else [None] * n

        # --- IMPORTANT: only keep rows that match the joint we asked for ---
        asked = str(nname).strip()
        for i in range(n):
            obj_i = str(Obj[i]).strip()
            if obj_i != asked:
                continue

            # also keep only the selected case name if it exists in results
            lc = str(LoadCase[i]).strip()
            if lc and case and lc != str(case):
                continue

            rows.append({
                "Node": obj_i,
                "Case": lc,
                "StepType": str(StepType[i]),
                "StepNum": StepNum[i],
                "UX": U1[i], "UY": U2[i], "UZ": U3[i],
                "RX": R1[i], "RY": R2[i], "RZ": R3[i],
            })

    return pd.DataFrame(rows)

def _collect_joint_displacements(model, node_names=None, case="Dead"):
    """
    Robust collector:
    1) Try single-call "all joints" query (fast)
    2) If it returns zero, fall back to per-joint calls (slow but reliable)
    """
    _select_case(model, case)

    # ---------- Attempt 1: single-call ----------
    out = None
    for joint_name in ("", "ALL"):
        try:
            out = model.Results.JointDispl(str(joint_name), 0)
            break
        except Exception:
            continue

    def _parse_jointdispl_out(out_obj):
        if not isinstance(out_obj, (list, tuple)):
            return pd.DataFrame()

        ret, n_header, base = _sap_results_header(out_obj)
        if base is None:
            return pd.DataFrame()

        obj_raw = out_obj[base + 0] if len(out_obj) > base + 0 else None
        try:
            n_actual = len(list(obj_raw)) if isinstance(obj_raw, (list, tuple)) else 0
        except Exception:
            n_actual = 0

        n = int(n_actual or (n_header or 0))
        if n <= 0:
            return pd.DataFrame()

        Obj      = _as_seq(out_obj[base + 0],  n)
        LoadCase = _as_seq(out_obj[base + 2],  n) if len(out_obj) > base + 2 else [None] * n
        StepType = _as_seq(out_obj[base + 3],  n) if len(out_obj) > base + 3 else [None] * n
        StepNum  = _as_seq(out_obj[base + 4],  n) if len(out_obj) > base + 4 else [None] * n

        U1 = _as_seq(out_obj[base + 5],  n) if len(out_obj) > base + 5 else [None] * n
        U2 = _as_seq(out_obj[base + 6],  n) if len(out_obj) > base + 6 else [None] * n
        U3 = _as_seq(out_obj[base + 7],  n) if len(out_obj) > base + 7 else [None] * n
        R1 = _as_seq(out_obj[base + 8],  n) if len(out_obj) > base + 8 else [None] * n
        R2 = _as_seq(out_obj[base + 9],  n) if len(out_obj) > base + 9 else [None] * n
        R3 = _as_seq(out_obj[base + 10], n) if len(out_obj) > base + 10 else [None] * n

        keep = None
        if node_names is not None:
            keep = set(str(x).strip() for x in node_names)

        rows = []
        for i in range(n):
            node = str(Obj[i]).strip()
            if not node:
                continue
            if keep is not None and node not in keep:
                continue

            lc = str(LoadCase[i]).strip()
            if case and lc and lc != str(case):
                continue

            rows.append({
                "Node": node,
                "Case": lc,
                "StepType": str(StepType[i]).strip(),
                "StepNum": StepNum[i],
                "UX": U1[i], "UY": U2[i], "UZ": U3[i],
                "RX": R1[i], "RY": R2[i], "RZ": R3[i],
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.drop_duplicates(subset=["Node", "Case", "StepType", "StepNum"], keep="last").reset_index(drop=True)
        return df

    df = _parse_jointdispl_out(out) if out is not None else pd.DataFrame()
    if not df.empty:
        return df

    # ---------- Attempt 2: fallback per-joint ----------
    if node_names is None:
        node_names = _get_all_point_names(model)

    return _collect_joint_displacements_per_node(model, node_names, case=case)


def _collect_frame_end_forces(model, frame_names, case="Dead"):
    rows = []
    _select_case(model, case)

    for fname in frame_names:
        out = None

        # Try common signatures
        for args in ((str(fname), 1, str(case)), (str(fname), 1), (str(fname), 1, 0, str(case))):
            try:
                out = model.Results.FrameForce(*args)
                break
            except Exception:
                continue

        if not isinstance(out, (list, tuple)) or len(out) < 12:
            continue

        ret, n_header, base = _sap_results_header(out)
        if base is None:
            continue

        obj_raw = out[base + 0] if len(out) > base + 0 else None
        try:
            n_actual = len(list(obj_raw)) if isinstance(obj_raw, (list, tuple)) else 0
        except Exception:
            n_actual = 0

        nres = int(n_actual or (n_header or 0))
        if nres <= 0:
            continue

        # Layout starting at base:
        # Obj, Elm, LoadCase, StepType, StepNum, P, V2, V3, T, M2, M3
        Obj      = _as_seq(out[base + 0], nres)
        Elm      = _as_seq(out[base + 1], nres)
        LoadCase = _as_seq(out[base + 2], nres)
        StepType = _as_seq(out[base + 3], nres)
        StepNum  = _as_seq(out[base + 4], nres)

        P  = _as_seq(out[base + 5], nres)
        V2 = _as_seq(out[base + 6], nres)
        V3 = _as_seq(out[base + 7], nres)
        T  = _as_seq(out[base + 8], nres)
        M2 = _as_seq(out[base + 9], nres)
        M3 = _as_seq(out[base + 10], nres)

        for i in range(nres):
            # SAP “ends only” often returns I/J alternating rows
            end = "I" if (i % 2 == 0) else "J"
            rows.append({
                "Frame": str(Obj[i]),
                "Elm": str(Elm[i]),
                "Case": str(LoadCase[i]),
                "StepType": str(StepType[i]),
                "StepNum": StepNum[i],
                "End": end,
                "P": P[i], "V2": V2[i], "V3": V3[i],
                "T": T[i], "M2": M2[i], "M3": M3[i],
            })

    return pd.DataFrame(rows)

def _collect_shell_forces_moments(model, area_names, case="Dead"):
    rows = []
    _select_case(model, case)

    # Prefer Object (0) first; if the model isn’t meshed the way we expect,
    # itemtype=1 can return ret=0 but nres=0.
    ITEMTYPE_CANDIDATES = (0, 1)  # 0=Object, 1=Element(mesh)
    api_methods = ["AreaForceShell", "AreaForceShell_1", "ShellForce"]

    fail_logs_left = 6
    ok_logs_left = 3
    best_itemtype_seen = None

    dbg_print_left = 5  # print raw head/tail for first 5 areas we successfully call

    def _try_call(fn, area_name, item_type):
        nonlocal fail_logs_left, ok_logs_left
        for args in ((str(area_name), int(item_type)),
                     (str(area_name), int(item_type), str(case))):
            try:
                out = fn(*args)
                if ok_logs_left > 0:
                    _log("[DEBUG] Shell probe ok:",
                         "case=", case, "area=", area_name,
                         "fn=", fn.__name__, "args=", args,
                         "out_len=", (len(out) if isinstance(out, (list, tuple)) else "n/a"))
                    ok_logs_left -= 1
                return out
            except Exception as e:
                if fail_logs_left > 0:
                    _log("[DEBUG] Shell probe fail:",
                         "case=", case, "area=", area_name,
                         "fn=", fn.__name__, "args=", args,
                         "err=", repr(e))
                    fail_logs_left -= 1
        return None

    def _infer_nres(out):
        """
        Infer nres robustly: prefer actual Obj-array length over header.
        Returns (nres, ret, n_header, base)
        """
        ret, n_header, base = _sap_results_header(out)
        if base is None:
            return 0, ret, n_header, base

        obj_raw = out[base + 0] if (isinstance(out, (list, tuple)) and len(out) > base + 0) else None
        try:
            n_actual = len(list(obj_raw)) if isinstance(obj_raw, (list, tuple)) else 0
        except Exception:
            n_actual = 0

        nres = int(n_actual or (n_header or 0))
        return nres, ret, n_header, base

    # de-dupe area list (prevents accidental repeats)
    seen = set()
    area_names = [a for a in area_names if not (str(a) in seen or seen.add(str(a)))]

    for an in area_names:
        out = None
        used_itemtype = None
        used_api = None
        used_base = None
        used_nres = 0

        # ---- find first API+itemtype combo that yields nres > 0 ----
        for it in ITEMTYPE_CANDIDATES:
            for mname in api_methods:
                fn = getattr(model.Results, mname, None)
                if fn is None:
                    continue

                out_try = _try_call(fn, an, it)
                if out_try is None:
                    continue

                nres_try, ret_try, n_header_try, base_try = _infer_nres(out_try)

                # ✅ IMPORTANT: do NOT accept “successful” calls that return 0 rows
                if nres_try <= 0:
                    if DEBUG:
                        _log("[DEBUG] Shell probe empty:",
                             "case=", case, "area=", an,
                             "api=", mname, "itemtype=", it,
                             "ret=", ret_try, "n_header=", n_header_try,
                             "nres=", nres_try, "base=", base_try)
                    continue

                out = out_try
                used_itemtype = it
                used_api = mname
                used_base = base_try
                used_nres = nres_try
                best_itemtype_seen = it
                break

            if out is not None:
                break

        if out is None:
            continue

        # ---- DEBUG: show the *actual* shape we're getting (first few successes) ----
        if DEBUG and dbg_print_left > 0:
            try:
                _log("[DEBUG] AreaForceShell raw head:", repr(out[:6]))
                _log("[DEBUG] AreaForceShell raw tail:", repr(out[-6:]))
                _log("[DEBUG] AreaForceShell types head:", [type(x).__name__ for x in out[:6]])
                _log("[DEBUG] AreaForceShell types tail:", [type(x).__name__ for x in out[-6:]])
                _log("[DEBUG] AreaForceShell chosen:", "area=", an, "api=", used_api,
                     "itemtype=", used_itemtype, "nres=", used_nres, "base=", used_base)
            except Exception as e:
                _log("[DEBUG] AreaForceShell debug print failed:", repr(e))
            dbg_print_left -= 1

        # ---- Parse ----
        try:
            # Recompute header/base (safe) and infer n again (robust)
            ret, n_header, base = _sap_results_header(out)
            if base is None:
                continue

            obj_raw = out[base + 0] if len(out) > base + 0 else None
            try:
                n_actual = len(list(obj_raw)) if isinstance(obj_raw, (list, tuple)) else 0
            except Exception:
                n_actual = 0

            nres = int(n_actual or (n_header or 0))
            if nres <= 0:
                continue

            # Layout starting at base:
            Obj      = _as_seq(out[base + 0],  nres)
            Elm      = _as_seq(out[base + 1],  nres)
            LoadCase = _as_seq(out[base + 2],  nres)
            StepType = _as_seq(out[base + 3],  nres)
            StepNum  = _as_seq(out[base + 4],  nres)

            F11 = _as_seq(out[base + 5],  nres)
            F22 = _as_seq(out[base + 6],  nres)
            F12 = _as_seq(out[base + 7],  nres)
            M11 = _as_seq(out[base + 8],  nres)
            M22 = _as_seq(out[base + 9],  nres)
            M12 = _as_seq(out[base + 10], nres)
            V13 = _as_seq(out[base + 11], nres)
            V23 = _as_seq(out[base + 12], nres)

            asked_case = str(case).strip()

            for i in range(nres):
                # Optional case filter (helps if API returns multiple cases)
                lc = str(LoadCase[i]).strip()
                if asked_case and lc and lc != asked_case:
                    continue

                f11 = float(F11[i]); f22 = float(F22[i]); f12 = float(F12[i])
                avg = 0.5 * (f11 + f22)
                rad = ((0.5 * (f11 - f22)) ** 2 + (f12 ** 2)) ** 0.5
                fmax = avg + rad
                fmin = avg - rad

                rows.append({
                    "Area": str(Obj[i]),
                    "Elm": str(Elm[i]),
                    "Case": lc,
                    "StepType": str(StepType[i]),
                    "StepNum": StepNum[i],
                    "F11": f11, "F22": f22, "F12": f12,
                    "Fmax": fmax, "Fmin": fmin,
                    "M11": float(M11[i]), "M22": float(M22[i]), "M12": float(M12[i]),
                    "V13": float(V13[i]), "V23": float(V23[i]),
                    "ItemTypeUsed": used_itemtype,
                    "APIUsed": used_api,
                })

        except Exception as e:
            if DEBUG:
                _log("[DEBUG] Shell parse exception:",
                     "case=", case, "area=", an,
                     "api=", used_api, "itemtype=", used_itemtype,
                     "err=", repr(e))
            continue

    df = pd.DataFrame(rows)
    _log("[DEBUG] Shell collect done:",
         "case=", case,
         "rows=", len(df),
         "unique_areas=", (0 if df.empty else df["Area"].nunique()),
         "best_itemtype=", best_itemtype_seen)
    return df

def _extrema_summary(shell_df, label):
    if shell_df is None or shell_df.empty:
        return []
    
    def _row(col, mode="max"):
        idx = shell_df[col].astype(float).idxmax() if mode=="max" else shell_df[col].astype(float).idxmin()
        r = shell_df.loc[idx].to_dict()
        r["Metric"] = f"{label}:{col}:{mode}"
        r["Value"] = r[col]
        return r
    return [
        _row("M11","max"),
        _row("V13","max"),
        _row("Fmax","max"),
        _row("Fmin","min"),
    ]


def _pick_key_nodes_for_settlement(nodes_df, axis="Z"):
    """
    Picks:
      - Vertex: max along vertical axis
      - Rim: nodes at min along vertical axis (corners + edges live here)
      - Corners: rim nodes farthest from centroid (top Ne points)
      - Edge midpoints: rim nodes nearest to directions between corner rays
    Returns dict of node name lists.
    """
    ax = (axis or "Z").upper()
    coord = {"X":"X","Y":"Y","Z":"Z"}[ax]

    # Vertex = max elevation along chosen vertical axis
    zmax = float(nodes_df[coord].max())
    vtx = nodes_df.loc[(nodes_df[coord] - zmax).abs() <= 1e-6, "Name"].astype(str).tolist()

    # Rim = min elevation
    zmin = float(nodes_df[coord].min())
    rim = nodes_df.loc[(nodes_df[coord] - zmin).abs() <= 1e-6, ["Name","X","Y"]].copy()
    if rim.empty:
        return {"vertex": vtx, "corners": [], "edges": []}

    cx = float(rim["X"].mean())
    cy = float(rim["Y"].mean())
    rim["r2"] = (rim["X"]-cx)**2 + (rim["Y"]-cy)**2

    # Corners = farthest 4 (works for your Ne=4 case; if Ne changes, use Ne)
    corners = rim.sort_values("r2", ascending=False).head(4)["Name"].astype(str).tolist()

    # Edge “midpoints” = rim nodes closest to angle halfway between corner angles
    # Simple: pick 4 rim nodes closest to each quadrant direction.
    import numpy as np
    rim["ang"] = np.arctan2((rim["Y"]-cy).to_numpy(), (rim["X"]-cx).to_numpy())
    rim["ang"] = (rim["ang"] + 2*np.pi) % (2*np.pi)

    target_angles = [np.pi/4, 3*np.pi/4, 5*np.pi/4, 7*np.pi/4]
    edges = []
    for ta in target_angles:
        rim["dang"] = np.minimum((rim["ang"]-ta) % (2*np.pi), (ta-rim["ang"]) % (2*np.pi))
        pick = rim.sort_values("dang", ascending=True).iloc[0]["Name"]
        edges.append(str(pick))

    # de-dupe edges if they collide
    edges = list(dict.fromkeys(edges))

    return {"vertex": vtx, "corners": corners, "edges": edges}


def _settlement_mm_from_displacements(disp_df, node_list, vertical_axis="Z"):
    if disp_df is None or disp_df.empty:
        return pd.DataFrame(columns=["Node","UZ_mm"])
    ax = (vertical_axis or "Z").upper()
    col = {"X":"UX","Y":"UY","Z":"UZ"}[ax]

    sub = disp_df.loc[disp_df["Node"].astype(str).isin([str(n) for n in node_list]), ["Node", col]].copy()
    sub = sub.rename(columns={col: "U_vert"})
    sub["UZ_mm"] = sub["U_vert"].astype(float) * 1000.0
    return sub[["Node","UZ_mm"]]


# ---------------- Area & Pattern Automation ---------------- #

def _get_all_area_names(model):
    try:
        return _filter_bad_sap_names(_sap_get_name_list(model.AreaObj.GetNameList()))
    except Exception:
        return []
    
def _get_all_point_names(model):
    try:
        return _filter_bad_sap_names(_sap_get_name_list(model.PointObj.GetNameList()))
    except Exception:
        return []


def _get_all_frame_names(model):
    try:
        return _filter_bad_sap_names(_sap_get_name_list(model.FrameObj.GetNameList()))
    except Exception:
        return []


def _get_joint_xyz(model, joint_name):
    try:
        _, x, y, z = model.PointObj.GetCoordCartesian(str(joint_name), "Global")
    except Exception:
        _, x, y, z = model.PointObj.GetCoordCartesian(str(joint_name))
    return float(x), float(y), float(z)


def _iter_all_point_coords(model):
    """
    Yield (name, x, y, z) for all joints in the model.
    Handles SAP2000 COM return-shape weirdness.
    """
    names_seq = _filter_bad_sap_names(_sap_get_name_list(model.PointObj.GetNameList()))
    if not names_seq:
        return

    for nm in names_seq:
        name_str = str(nm)
        try:
            out_coord = model.PointObj.GetCoordCartesian(name_str, "Global")
        except Exception:
            out_coord = model.PointObj.GetCoordCartesian(name_str)

        if isinstance(out_coord, (list, tuple)) and len(out_coord) >= 4:
            _, x, y, z = out_coord[:4]
        else:
            continue

        yield name_str, float(x), float(y), float(z)


def _set_area_axes_by_two_joints(model, joint1="1", joint2="23", plane="31", replace=True):
    """
    Emulates UI: Assign > Area > Local Axes... > Specify Advanced Local Axes
    Plane ≈ 3-1, Two Joints = (joint1, joint2).
    Tries advanced API variants, falls back to a 2D rotation (in XY).
    """
    area = model.AreaObj
    names = _get_all_area_names(model)
    if not names:
        return

    for an in names:
        for meth in ("SetLocalAxesAdvanced", "SetLocalAxes_2", "SetLocalAxesByPoints"):
            try:
                m = getattr(area, meth)
            except AttributeError:
                continue

            try:
                plane_code = 3
                if meth == "SetLocalAxesAdvanced":
                    axis_dir = 1
                    m(an, plane_code, axis_dir, True, str(joint1), str(joint2), bool(replace))
                elif meth == "SetLocalAxesByPoints":
                    m(an, plane_code, str(joint1), str(joint2), bool(replace))
                else:
                    axis_dir = 1
                    m(an, plane_code, axis_dir, True, str(joint1), str(joint2), bool(replace))
                break
            except Exception:
                continue
        else:
            try:
                x1, y1, _ = _get_joint_xyz(model, joint1)
                x2, y2, _ = _get_joint_xyz(model, joint2)
                angle_deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
                try:
                    area.SetLocalAxes(an, float(angle_deg))
                except Exception:
                    area.SetLocalAxes(an, float(angle_deg), "Global")
            except Exception:
                pass


# --- new helpers for extracting full model --- #

def _infer_ne_from_filename(xlsx_path):
    """
    Try to infer the number of tympans (Ne) from the input filename.
    Expected pattern: Hypar4_H2.0_R1.0_N20.xlsx -> first token ends with digits (4)
    """
    base = os.path.splitext(os.path.basename(xlsx_path))[0]
    first = base.split("_")[0]
    m = re.search(r"(\d+)$", first)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _iter_all_frames(model):
    """Yield (name, I, J, Section) for all frame objects."""

    names = _get_all_frame_names(model)

    for nm in names:
        i_pt = j_pt = None
        sec = None
        try:
            ret, i_pt, j_pt = model.FrameObj.GetPoints(nm)
        except Exception:
            pass
        try:
            ret, sec = model.FrameObj.GetSection(nm)
        except Exception:
            try:
                ret, sec = model.FrameObj.GetProperty(nm)
            except Exception:
                sec = None
        yield nm, i_pt, j_pt, sec


def _iter_all_areas(model):
    """Yield (name, [points...], section) for all area objects."""
    for an in _get_all_area_names(model):
        pts = []
        sec = None
        try:
            ret, num_pts, names = model.AreaObj.GetPoints(an)
            names = _as_seq(names, num_pts)
            pts = [str(nm) for nm in names[: int(num_pts)]]
        except Exception:
            pts = []
        try:
            ret, sec = model.AreaObj.GetProperty(an)
        except Exception:
            sec = None
        yield an, pts, sec


def _extract_model_to_dfs(model):
    """
    Robustly extract the *actual* SAP model into DataFrames.
    Handles SAP2000 COM return-shape weirdness across versions.
    """

    # 🔍 TEMP DEBUG — inspect raw COM return shapes (enable only when needed)
    if DEBUG_COM_SHAPES and DEBUG:
        try:
            _log("RAW PointObj.GetNameList:", repr(model.PointObj.GetNameList()))
            _log("RAW AreaObj.GetNameList:", repr(model.AreaObj.GetNameList()))
            _log("RAW FrameObj.GetNameList:", repr(model.FrameObj.GetNameList()))
        except Exception as e:
            _log("RAW NameList debug failed:", e)

    def _coord(nm: str):
        for args in [(nm, "Global"), (nm,)]:
            try:
                res = model.PointObj.GetCoordCartesian(*args)
            except Exception:
                continue

            if not isinstance(res, (tuple, list)):
                continue

            # common: (ret, x, y, z)
            if len(res) >= 4:
                try:
                    ret = int(res[0])
                    if ret == 0:
                        return float(res[1]), float(res[2]), float(res[3])
                except Exception:
                    pass

            # fallback: (x, y, z)
            if len(res) >= 3:
                try:
                    return float(res[0]), float(res[1]), float(res[2])
                except Exception:
                    pass
        return None

    # --- Points ---
    pt_names = _get_all_point_names(model)

    nodes_rows = []
    for nm in pt_names:
        name = str(nm)
        xyz = _coord(name)
        if xyz is None:
            continue
        x, y, z = xyz
        nodes_rows.append([name, x, y, z])

    nodes_df = pd.DataFrame(nodes_rows, columns=["Name", "X", "Y", "Z"])

    # --- Frames ---
    frame_names = _get_all_frame_names(model)

    elems_rows = []
    for fn in frame_names:
        fn = str(fn)
        try:
            res = model.FrameObj.GetPoints(fn)
        except Exception:
            continue

        if isinstance(res, (tuple, list)) and len(res) >= 3:
            pi, pj = res[-2], res[-1]
        elif isinstance(res, (tuple, list)) and len(res) == 2:
            pi, pj = res
        else:
            continue

        elems_rows.append([fn, str(pi), str(pj)])

    elems_df = pd.DataFrame(elems_rows, columns=["Frame", "I", "J"])

    # --- Areas ---
    area_names = _get_all_area_names(model)

    areas_rows = []
    for an in area_names:
        an = str(an)

        try:
            res = model.AreaObj.GetPoints(an)
        except Exception:
            continue

        if not isinstance(res, (list, tuple)):
            continue

        # common shapes:
        # (npts, pts) OR (ret, npts, pts)
        if len(res) == 2:
            npts, pts = res[0], res[1]
        elif len(res) >= 3:
            # often (ret, npts, pts) but sometimes other variants
            npts, pts = res[-2], res[-1]
        else:
            continue

        try:
            pts_list = list(pts) if pts is not None else []
        except Exception:
            pts_list = [pts] if pts is not None else []

        # Flatten one level if nested like (('1','2','3','4'),)
        if len(pts_list) == 1 and isinstance(pts_list[0], (list, tuple)):
            pts_list = list(pts_list[0])

        # 🔹 NEW: trim to reported number of points
        try:
            n = int(float(npts))
            pts_list = pts_list[:n]
        except Exception:
            pass

        pts_list = [p for p in pts_list if str(p).strip().lower() not in ("", "none", "nan")]

        # pad to 4 (SAP shells expect up to 4)
        while len(pts_list) < 4:
            pts_list.append("")

        areas_rows.append([an] + [str(p) for p in pts_list[:4]])
    
    areas_df = pd.DataFrame(areas_rows, columns=["Area", "P1", "P2", "P3", "P4"])

    return nodes_df, elems_df, areas_df


# ---------------- Soil / pattern automation ---------------- #

def _ensure_joint_pattern_exists(model, pattern_name="SOIL_DEPTH"):
    candidates = [
        ("PatternDef", "SetJointPattern"),
        ("EditGeneral", "SetJointPattern"),
        ("DefineJointPattern", None),
    ]
    for obj_name, meth_name in candidates:
        try:
            target = getattr(model, obj_name) if obj_name else model
            method = getattr(target, meth_name) if meth_name else getattr(model, "DefineJointPattern")
            method(pattern_name)
            return True
        except Exception:
            continue
    return False


def _set_joint_pattern_value_for_point(model, joint_name, pattern_name, value):
    point = model.PointObj
    method_names = ["SetPatternValue", "SetPattern", "SetPatternValue_1"]
    for m in method_names:
        try:
            getattr(point, m)(str(joint_name), str(pattern_name), float(value))
            return True
        except Exception:
            continue
    return False


def _assign_area_surface_pressure_by_joint_pattern(
    model,
    area_name,
    load_pattern,
    joint_pattern,
    multiplier,
    coord_sys="Global",
    replace=True
):
    """
    Assign Area Loads -> Surface Pressure -> By Joint Pattern.
    Returns True only if SAP returns ret == 0.
    """
    area = model.AreaObj

    # --- Attempt 1: Surface Pressure by Joint Pattern ---
    try:
        ret = area.SetLoadSurfacePressure(
            area_name,
            load_pattern,
            "Projected",
            float(multiplier),
            coord_sys,
            bool(replace),
            True,
            joint_pattern
        )
        _log("[DEBUG] pressure assign ret=", ret, "area=", area_name, "p=", multiplier)
        return ret == 0
    except Exception as e:
        _log("[DEBUG] SetLoadSurfacePressure EXCEPTION area=", area_name, e)

    # --- Attempt 2: Uniform to Joint Pattern ---
    try:
        ret = area.SetLoadUniformToPattern(
            area_name,
            load_pattern,
            joint_pattern,
            coord_sys,
            float(multiplier),
            bool(replace)
        )
        _log("[DEBUG] uniform-to-pattern ret=", ret, "area=", area_name, "p=", multiplier)
        return ret == 0
    except Exception as e:
        _log("[DEBUG] SetLoadUniformToPattern EXCEPTION area=", area_name, e)

    # --- Attempt 3: Plain uniform load (last-resort) ---
    try:
        ret = area.SetLoadUniform(
            area_name,
            load_pattern,
            float(multiplier),
            coord_sys,
            bool(replace)
        )
        _log("[DEBUG] uniform ret=", ret, "area=", area_name, "p=", multiplier)
        return ret == 0
    except Exception as e:
        _log("[DEBUG] SetLoadUniform EXCEPTION area=", area_name, e)
        return False


def sweep_soil_pressure_by_depth(
    model,
    depth_min=0.0,
    depth_max=5.0,
    depth_step=0.5,
    gamma_soil_N_per_m3=18000.0,
    load_pattern_name="SOIL",
    joint_pattern_name="SOIL_DEPTH",
    vertical_axis="Z",
    normalize_pattern=False,
    results_case_name="SOIL_CASE",
    replace_area_load_each_step=True,
    visible=True,
    close_after=False,
    results_book_path=None,
):
    """
    Sweep soil pressure by depth (0.5 m steps by default) and collect results.
    Assigns Area Loads -> Surface Pressure -> By Joint Pattern using a joint-pattern
    whose value is the local depth (or normalized depth) at each joint.
    """
    if depth_step <= 0:
        raise ValueError("depth_step must be > 0")
    if depth_min > depth_max:
        depth_min, depth_max = depth_max, depth_min

    try:
        model.LoadPatterns.Add(load_pattern_name, 1, 0.0)
    except Exception:
        pass
    _ensure_joint_pattern_exists(model, joint_pattern_name)

    area_names = _get_all_area_names(model)
    if not area_names:
        raise RuntimeError("No area (shell) objects found. Soil pressure must be assigned to areas.")

    joints = list(_iter_all_point_coords(model))
    if not joints:
        raise RuntimeError("No joints found in model to set joint-pattern values.")

    ax = vertical_axis.upper()
    if ax not in ("X", "Y", "Z"):
        raise ValueError("vertical_axis must be one of 'X','Y','Z'")

    if ax == "X":
        surface_elev = max(x for _, x, _, _ in joints)
        axis_index = 0
    elif ax == "Y":
        surface_elev = max(y for _, _, y, _ in joints)
        axis_index = 1
    else:
        surface_elev = max(z for _, _, _, z in joints)
        axis_index = 2

    try:
        model.LoadCases.StaticLinear.SetCase(results_case_name)
        model.LoadCases.StaticLinear.SetLoads(results_case_name, 1, [load_pattern_name], [1.0])
    except Exception:
        pass

    results = []
    d = float(depth_min)

    while d <= depth_max + 1e-9:
        d = round(d, 6)

        if normalize_pattern:
            multiplier = gamma_soil_N_per_m3 * d
        else:
            multiplier = gamma_soil_N_per_m3

        for joint_name, x, y, z in joints:
            elev = (x, y, z)[axis_index]
            raw_depth = max(0.0, surface_elev - elev)
            depth_for_step = min(raw_depth, d)

            if normalize_pattern:
                pattern_value = 0.0 if d <= 1e-12 else (depth_for_step / d)
            else:
                pattern_value = depth_for_step

            _set_joint_pattern_value_for_point(
                model, joint_name, joint_pattern_name, pattern_value
            )

        for an in area_names:
            _assign_area_surface_pressure_by_joint_pattern(
                model,
                area_name=an,
                load_pattern=load_pattern_name,
                joint_pattern=joint_pattern_name,
                multiplier=multiplier,
                coord_sys="Global",
                replace=replace_area_load_each_step
            )

        _run_analysis(model)

        node_names = [n for (n, _, _, _) in joints]

        try:
            disp_df = _collect_joint_displacements(model, node_names, case=results_case_name)

        except Exception:
            disp_df = _collect_joint_displacements(model, node_names, case="Dead")

        _log("[DEBUG] JointDispl rows:", 0 if disp_df is None else len(disp_df))

        force_df = pd.DataFrame()

        if disp_df is None:
            disp_df = pd.DataFrame()

        disp_df.insert(0, "Depth_m", d)
        disp_df.insert(1, "Multiplier", multiplier)

        if not force_df.empty:
            force_df.insert(0, "Depth_m", d)
            force_df.insert(1, "Multiplier", multiplier)

        results.append((d, disp_df, force_df))
        d = round(d + depth_step, 6)

    if results_book_path:
        _ensure_xlsxwriter()
        with pd.ExcelWriter(results_book_path, engine="xlsxwriter") as xlw:
            for depth_val, disp_df, force_df in results:
                dtag = f"d{str(depth_val).replace('.', '_')}"
                if not disp_df.empty:
                    disp_df.to_excel(xlw, sheet_name=f"Disp_{dtag}", index=False)
                if not force_df.empty:
                    force_df.to_excel(xlw, sheet_name=f"Forces_{dtag}", index=False)

    return {
        "steps": len(results),
        "depths": [d for d, _, _ in results],
        "displacements_by_step": [disp for _, disp, _ in results],
        "forces_by_step": [frc for _, _, frc in results],
    }


def _set_joint_vertical_spring(model, joint_name, k_vert_Npm, vertical_axis="Z", replace=True):
    """
    Assign uncoupled vertical spring stiffness at a joint.
    k_vert_Npm: N/m
    """
    ax = (vertical_axis or "Z").upper()
    dof = 2 if ax == "Z" else (1 if ax == "Y" else 0)  # U1=X, U2=Y, U3=Z

    K6 = [0.0] * 6
    K6[dof] = float(k_vert_Npm)

    try:
        model.PointObj.SetSpring(str(joint_name), K6)
        return True
    except Exception:
        pass

    try:
        model.PointObj.SetSpring(str(joint_name), K6, 0, True, bool(replace))
        return True
    except Exception:
        return False


def _assign_soil_stiffness_as_base_springs(model, nodes_df, E_soil_mpa, vertical_axis="Z"):
    """
    Use E_soil (MPa) to create Winkler-like support springs at base joints.

    Simple model assumption:
      E_soil(Pa) = E_soil_mpa * 1e6
      footprint area A from convex hull of base nodes in XY
      L = sqrt(A) (clamped) length scale
      k_subgrade (N/m^3) = E_soil / L
      Atrib = A / n_base_nodes
      K_joint (N/m) = k_subgrade * Atrib
    """
    E_pa = float(E_soil_mpa) * 1e6
    if E_pa <= 0:
        return {"assigned": 0, "E_pa": E_pa, "k_subgrade": None, "footprint_area": None, "L": None}

    zmin = float(nodes_df["Z"].min())
    TOL_Z = 1e-3
    
    base = nodes_df.loc[(nodes_df["Z"] - zmin).abs() <= TOL_Z, ["Name", "X", "Y"]].copy()
    if base.empty:
        return {"assigned": 0, "E_pa": E_pa, "k_subgrade": None, "footprint_area": None, "L": None}

    pts = np.unique(base[["X", "Y"]].astype(float).values, axis=0)
    if len(pts) < 3:
        return {"assigned": 0, "E_pa": E_pa, "k_subgrade": None, "footprint_area": 0.0, "L": None}

    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(tuple(p))
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(tuple(p))
    hull = lower[:-1] + upper[:-1]

    area2 = 0.0
    for i in range(len(hull)):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % len(hull)]
        area2 += x1 * y2 - x2 * y1
    A = abs(area2) * 0.5

    n_base = len(base)
    if A <= 0 or n_base <= 0:
        return {"assigned": 0, "E_pa": E_pa, "k_subgrade": None, "footprint_area": A, "L": None}

    L = max(0.5, (A ** 0.5))
    k_subgrade = E_pa / L
    Atrib = A / float(n_base)
    K_joint = k_subgrade * Atrib

    assigned = 0
    for nm in base["Name"].astype(str).tolist():
        if _set_joint_vertical_spring(model, nm, K_joint, vertical_axis=vertical_axis, replace=True):
            assigned += 1

    return {
        "assigned": assigned,
        "E_pa": E_pa,
        "k_subgrade": k_subgrade,
        "footprint_area": A,
        "L": L,
        "K_joint": K_joint
    }

def _assign_constant_overburden_to_all_areas(
    model,
    pressure_Npm2=OVERBURDEN_PRESSURE_NPM2,
    load_pattern=SOIL_LOAD_PATTERN,
    case_name=SOIL_CASE_NAME,
    coord_sys="Global",
    replace=True
):
    area_names = _get_all_area_names(model)
    if not area_names:
        return {"assigned_areas": 0}

    try:
        model.LoadPatterns.Add(load_pattern, 8, 0.0)
    except Exception:
        pass

    try:
        model.LoadCases.StaticLinear.SetCase(case_name)
        model.LoadCases.StaticLinear.SetLoads(case_name, 1, [load_pattern], [1.0])
    except Exception:
        pass

    assigned = 0
    for an in area_names:
        try:
            model.AreaObj.SetLoadSurfacePressure(
                str(an),
                load_pattern,
                "Projected",
                float(pressure_Npm2),
                coord_sys,
                bool(replace)
            )
            assigned += 1
        except Exception:
            pass

    return {
        "assigned_areas": assigned,
        "pressure": float(pressure_Npm2),
        "pattern": load_pattern,
        "case": case_name
    }

def _assign_depth_based_overburden_to_all_areas(
    model,
    burial_depth_vertex_m=1.0,
    gamma_soil_Npm3=18000.0,
    vertical_axis="Z",
    load_pattern=SOIL_LOAD_PATTERN,
    case_name=SOIL_CASE_NAME,
    replace=True,
):
    # 1) Start fresh logs
    _reset_load_logs()

    # Confirm load pattern creation (log ret)
    try:
        ret_lp = model.LoadPatterns.Add(load_pattern, 8, 0.0)
        _log("[DEBUG] LoadPatterns.Add:", load_pattern, "ret=", ret_lp)
    except Exception as e:
        _log("[DEBUG] LoadPatterns.Add EXC:", load_pattern, "err=", repr(e))

    # Create / define the case
    try:
        ret_case = model.LoadCases.StaticLinear.SetCase(case_name)
        _log("[DEBUG] StaticLinear.SetCase:", case_name, "ret=", ret_case)
    except Exception as e:
        _log("[DEBUG] StaticLinear.SetCase EXC:", case_name, "err=", repr(e))

    # Attach the load pattern to the case
    try:
        # Some SAP v20 COM type libraries declare the SF array as BSTR (string),
        # so passing floats throws: "unicode string expected instead of float instance".
        # We'll try a couple safe variants.

        ret_loads = None
        err_last = None

        # Variant A: lists, but SF as strings
        try:
            ret_loads = model.LoadCases.StaticLinear.SetLoads(case_name, 1, [str(load_pattern)], [str(1.0)])
        except Exception as e:
            err_last = e

        # Variant B: tuples, SF as strings (often better for COM SAFEARRAY)
        if ret_loads is None:
            try:
                ret_loads = model.LoadCases.StaticLinear.SetLoads(case_name, 1, (str(load_pattern),), (str(1.0),))
            except Exception as e:
                err_last = e

        # Variant C: sometimes COM wants scalar pattern + scalar SF
        if ret_loads is None:
            try:
                ret_loads = model.LoadCases.StaticLinear.SetLoads(case_name, 1, str(load_pattern), str(1.0))
            except Exception as e:
                err_last = e

        _log("[DEBUG] StaticLinear.SetLoads:", case_name, "pattern=", load_pattern, "ret=", ret_loads)

        # If we never got a ret, it failed all signatures
        if ret_loads is None:
            _log("[DEBUG] StaticLinear.SetLoads FAILED (all signatures). last_err=", repr(err_last))
            return {
                "assigned_areas": 0,
                "failed_areas": 0,
                "case": case_name,
                "pattern": load_pattern,
                "error": f"SetLoads exception: {repr(err_last)}"
            }

        # IMPORTANT: stop early if SetLoads returned nonzero
        if isinstance(ret_loads, (int, float)) and int(ret_loads) != 0:
            _log("[DEBUG] StaticLinear.SetLoads FAILED -> case will not output. ret=", ret_loads)
            return {
                "assigned_areas": 0,
                "failed_areas": 0,
                "case": case_name,
                "pattern": load_pattern,
                "error": f"SetLoads ret={ret_loads}"
            }

    except Exception as e:
        _log("[DEBUG] StaticLinear.SetLoads EXC:", case_name, "err=", repr(e))
        return {
            "assigned_areas": 0,
            "failed_areas": 0,
            "case": case_name,
            "pattern": load_pattern,
            "error": f"SetLoads exception: {repr(e)}"
        }



    # 2) Collect all joints + figure out "vertex" elevation
    joints = list(_iter_all_point_coords(model))
    if not joints:
        return {"assigned_areas": 0, "assigned_joints": 0}

    ax = (vertical_axis or "Z").upper()
    axis_index = 2 if ax == "Z" else (1 if ax == "Y" else 0)

    z_vertex = max((x, y, z)[axis_index] for _, x, y, z in joints)
    z_grade = z_vertex + float(burial_depth_vertex_m)

    # Quick lookup: point -> elevation
    elev_by_point = {}
    for name, x, y, z in joints:
        elev_by_point[str(name)] = float((x, y, z)[axis_index])

    area_names = _get_all_area_names(model)

    _log("[DEBUG] Overburden applying to first areas:", area_names[:5], "count=", len(area_names))

    # 4) For each area, compute avg depth and assign uniform pressure = gamma * depth
    assigned_areas = 0
    failed_areas = 0

    dbg_left = 5  # show first 5 areas' computed depth/pressure

    for an in area_names:
        an = str(an)

        try:
            res = model.AreaObj.GetPoints(an)
        except Exception:
            failed_areas += 1
            continue

        # normalize common shapes: (ret,npts,pts) OR (npts,pts,ret) OR (npts,pts)
        ret = 0
        npts = None
        pt_names = None

        if isinstance(res, (list, tuple)):
            if len(res) == 3:
                a, b, c = res
                # common: (ret, npts, pts)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and isinstance(c, (list, tuple)):
                    ret, npts, pt_names = int(a), int(b), list(c)
                # your build often: (npts, pts, ret)
                elif isinstance(a, (int, float)) and isinstance(b, (list, tuple)) and isinstance(c, (int, float)):
                    npts, pt_names, ret = int(a), list(b), int(c)
                else:
                    # fallback: last list-like is points; first numeric is npts
                    pt_names = next((x for x in res if isinstance(x, (list, tuple))), None)
                    npts = next((x for x in res if isinstance(x, (int, float))), None)
                    ret = 0
            elif len(res) == 2:
                a, b = res
                if isinstance(a, (int, float)) and isinstance(b, (list, tuple)):
                    npts, pt_names = int(a), list(b)
                    ret = 0
                else:
                    failed_areas += 1
                    continue
            else:
                failed_areas += 1
                continue
        else:
            failed_areas += 1
            continue

        if ret != 0 or not pt_names:
            failed_areas += 1
            continue

        # trim to npts (if we have it)
        if npts is not None:
            try:
                n = int(npts)
                if n > 0:
                    pt_names = list(pt_names)[:n]
            except Exception:
                pass

        # normalize + remove blanks
        pt_names = [str(p).strip() for p in pt_names if str(p).strip().lower() not in ("", "none", "nan")]

        elevs = [elev_by_point.get(p) for p in pt_names]
        elevs = [e for e in elevs if e is not None]
        if not elevs:
            failed_areas += 1
            continue

        elev_avg = sum(elevs) / len(elevs)
        depth_m = max(0.0, z_grade - elev_avg)
        pressure_Npm2 = float(gamma_soil_Npm3) * float(depth_m)

        if dbg_left > 0:
            _log("[DEBUG] depth_probe:",
                "area=", an,
                "z_grade=", z_grade,
                "elev_avg=", elev_avg,
                "depth_m=", depth_m,
                "pressure_Npm2=", pressure_Npm2)
            dbg_left -= 1

        ok = _assign_area_surface_pressure_by_uniform(
            model,
            area_name=an,
            load_pattern=load_pattern,
            pressure_Npm2=pressure_Npm2,
            coord_sys="Global",
            replace=bool(replace)
        )

        if ok:
            assigned_areas += 1
        else:
            failed_areas += 1
    
    _log_load_summary()

    _log(f"[DEBUG] Overburden assigned_areas={assigned_areas} failed_areas={failed_areas}")

    return {
        "assigned_areas": int(assigned_areas),
        "failed_areas": int(failed_areas),
        "gamma_Npm3": float(gamma_soil_Npm3),
        "burial_depth_vertex_m": float(burial_depth_vertex_m),
        "z_vertex": float(z_vertex),
        "z_grade": float(z_grade),
        "pattern": load_pattern,
        "case": case_name,
        "joint_pattern": None,   # not used in this version
        "assigned_joints": 0     # not used in this version
    }


def _ensure_case_runs_in_analysis(model, case_name):
    try:
        analyze = model.Analyze

        for meth in ("SetRunCaseFlag", "SetRunCaseFlag_1"):
            try:
                fn = getattr(analyze, meth)
            except AttributeError:
                continue

            try:
                ret = fn(str(case_name), True, True)
                _log("[DEBUG] Analyze."+meth+":", case_name, "ret=", ret)
                return (ret == 0)
            except TypeError:
                # some builds use (CaseName, Run) only
                try:
                    ret = fn(str(case_name), True)
                    _log("[DEBUG] Analyze."+meth+"(2-arg):", case_name, "ret=", ret)
                    return (ret == 0)
                except Exception as e:
                    _log("[DEBUG] Analyze."+meth+" failed:", case_name, "err=", repr(e))
            except Exception as e:
                _log("[DEBUG] Analyze."+meth+" failed:", case_name, "err=", repr(e))

        _log("[DEBUG] No SetRunCaseFlag method available on this build.")
        return False

    except Exception as e:
        _log("[DEBUG] _ensure_case_runs_in_analysis failed:", repr(e))
        return False


def run_sap2000_analysis(input_xlsx, visible=True, close_after=False, soil=None, material=None):
    """
    Build & analyze a model from 'Nodes', optional 'Elements', and optional 'Areas' sheets,
    then write results to: <input_basename>_results.xlsx

    Frames (line objects) are optional – a pure shell model is allowed.

    soil dict expected (for new backend soil workflow):
      soil = {
        "E_soil_mpa": <float>,    # USER enters this in GUI (MPa)
        "axis": "Z"               # optional, default "Z"
      }

    Note: constant overburden is BACKEND fixed as OVERBURDEN_PRESSURE_NPM2.
    """

    if not os.path.exists(input_xlsx):
        raise FileNotFoundError(input_xlsx)

    _reset_fail_counter()
    _ensure_openpyxl()

    # ---- read Excel ----
    nodes_in = _read_nodes_sheet(input_xlsx)
    elems_in = _read_elements_sheet(input_xlsx)

    try:
        areas_in = _read_areas_sheet(input_xlsx)
        _log("[DEBUG] areas_in rows:", 0 if areas_in is None else len(areas_in))

        # If the sheet exists but contains 0 data rows → treat as None
        if areas_in is not None and areas_in.empty:
            areas_in = None

    except Exception:
        areas_in = None

    # ---- FAST FAIL: Areas must exist for shells ----
    if areas_in is None:
        raise RuntimeError("Areas sheet is missing or empty — no shell objects will be created.")
    
    _log(
        "Bounds:",
        f"X [{nodes_in['X'].min()}, {nodes_in['X'].max()}], "
        f"Y [{nodes_in['Y'].min()}, {nodes_in['Y'].max()}], "
        f"Z [{nodes_in['Z'].min()}, {nodes_in['Z'].max()}]"
    )

    if nodes_in.empty:
        raise ValueError("Nodes sheet is empty.")

    _ = _infer_ne_from_filename(input_xlsx) or 4  # kept for compatibility if you use later

    sap, model = _start_sap2000_v20(visible=visible)
    try:
        # ---- material + build ----
        selected_material = _ensure_material_defined(model, material) if material else None
        default_mat = selected_material or "CONC40"

        _build_model_from_excel(
            model,
            nodes_in,
            elems_in,
            default_section=("RECT_300x500", default_mat, (0.30, 0.50))
        )

        if areas_in is not None:
            _build_areas_from_excel(
                model,
                areas_in,
                default_section=("SHELL_200", default_mat, 0.20)
            )

            area_live = _get_all_area_names(model)
            _log("[DEBUG] SAP area objects (live):", len(area_live))
            _log("[DEBUG] First 10 SAP area names:", area_live[:10])

            # 🔎 NEW: verify the first few areas actually have a shell property
            for an in area_live[:5]:
                try:
                    res = model.AreaObj.GetProperty(str(an))
                    _log("[DEBUG] AreaObj.GetProperty:", "area=", an, "res=", repr(res))
                except Exception as e:
                    _log("[DEBUG] AreaObj.GetProperty FAILED:", "area=", an, "err=", repr(e))

            # Orient area local axes...
            try:
                nodes_tot = len(nodes_in)
                E = int(round(nodes_tot ** 0.5)) - 1
                behind_name = str(E + 3)
                _set_area_axes_by_two_joints(model, joint1="1", joint2=behind_name, plane="31", replace=True)
            except Exception:
                _set_area_axes_by_two_joints(model, joint1="1", joint2="23", plane="31", replace=True)

        # Extract the actual built model (what SAP really has)
        nodes, elems, areas = _extract_model_to_dfs(model)

        _log("[DEBUG] First 10 SAP area names:", _get_all_area_names(model)[:10])

        _log("[DEBUG] Extracted:", f"nodes={len(nodes)} elems={len(elems)} areas_df={0 if areas is None else len(areas)}")
        _log("[DEBUG] SAP area objects:", len(_get_all_area_names(model)))

        # ---- supports / restraints ----
        if soil:
            # Fix UX, UY; leave UZ free so the vertical spring can act
            _fix_base_nodes(model, nodes, fix=(1, 1, 0, 1, 1, 1))
        else:
            # No-soil model: fully fixed base
            _fix_base_nodes(model, nodes)

        # ---- load patterns ----
        _add_default_self_weight(model, "Dead", 1.0)

        # ---- Soil actions: stiffness-based support + depth-based overburden ----
        soil_meta = None
        if soil:
            try:
                E_mpa = float(soil.get("E_soil_mpa", 0.0))
                vax = soil.get("axis", "Z")

                springs_meta = _assign_soil_stiffness_as_base_springs(
                    model, nodes, E_mpa, vertical_axis=vax
                )

                d_vertex = float(soil.get("burial_depth_vertex_m", 1.0))
                gamma = float(soil.get("gamma_Npm3", 18000.0))

                overburden_meta = _assign_depth_based_overburden_to_all_areas(
                    model,
                    burial_depth_vertex_m=d_vertex,
                    gamma_soil_Npm3=gamma,
                    vertical_axis=soil.get("axis", "Z"),
                    load_pattern=SOIL_LOAD_PATTERN,
                    case_name=SOIL_CASE_NAME,
                    replace=True
                )

                soil_meta = {"springs": springs_meta, "overburden": overburden_meta}

                _log("[Soil] " + json.dumps(
                    {"springs": springs_meta, "overburden": overburden_meta},
                    default=str
                ))
            except Exception as e:
                _log("[Soil] Skipped:", e)

        # ---- Save model beside spreadsheet ----
        base, _ext = os.path.splitext(input_xlsx)
        sdb_path = base + ".sdb"
        try:
            model.File.Save(sdb_path)
        except Exception:
            pass

        # Force run flags
        _ensure_case_runs_in_analysis(model, "Dead")
        if soil:
            _ensure_case_runs_in_analysis(model, SOIL_CASE_NAME)

        # Run analysis (Dead + Soil case if defined)
        _run_analysis(model)

        try:
            res = model.Results.Setup.GetCaseSelectedForOutput()
            _log("[DEBUG] Cases selected for output:", repr(res))
        except Exception as e:
            _log("[DEBUG] GetCaseSelectedForOutput failed:", repr(e))

        # Post-run verification (did SOIL_CASE actually generate results?)
        if soil:
            ok = _case_has_any_joint_displ_output(model, SOIL_CASE_NAME)
            _log(f"[DEBUG] SOIL_CASE produced output? {ok}")

        # Default: output "Dead"
        try:
            model.Results.Setup.DeselectAllCasesAndCombosForOutput()
            model.Results.Setup.SetCaseSelectedForOutput("Dead")
        except Exception:
            pass

        # --- Optional soil sweep (legacy/extra feature; independent of constant overburden) ---
        soil_results = None
        if soil:
            try:
                replace_area_load_each_step = soil.get("replace_each_step", True)
                if any(k in soil for k in ("depth_min", "depth_max", "depth_step", "gamma", "load_pattern", "joint_pattern", "normalize", "case_name")):
                    soil_results = sweep_soil_pressure_by_depth(
                        model,
                        depth_min=soil.get("depth_min", 0.0),
                        depth_max=soil.get("depth_max", 4.0),
                        depth_step=soil.get("depth_step", 0.5),
                        gamma_soil_N_per_m3=soil.get("gamma", 18000.0),
                        load_pattern_name=soil.get("load_pattern", "SOIL"),
                        joint_pattern_name=soil.get("joint_pattern", "SOIL_DEPTH"),
                        vertical_axis=soil.get("axis", "Z"),
                        normalize_pattern=soil.get("normalize", False),
                        results_case_name=soil.get("case_name", "SOIL_CASE"),
                        replace_area_load_each_step=replace_area_load_each_step,
                        visible=visible,
                        close_after=False,
                        results_book_path=None
                    )
            except Exception as e:
                _log("[SoilSweep] Skipped:", e)

        # ---- Collect results ----
        node_names = nodes["Name"].astype(str).tolist()
        frame_names = elems["Frame"].astype(str).tolist() if ("Frame" in elems.columns and len(elems) > 0) else []

        import traceback
        try:
            disp_df = _collect_joint_displacements(model, node_names, "Dead")

            _log("[DEBUG] SAP area objects (live):", len(_get_all_area_names(model)))

            _log("[DEBUG] JointDispl rows:", 0 if disp_df is None else len(disp_df))

            force_df = _collect_frame_end_forces(model, frame_names, "Dead") if frame_names else pd.DataFrame()
        except Exception:
            _log("[Error] While collecting results:")
            _log(traceback.format_exc())
            raise

        # Soil-only response (constant overburden case)
        soil_disp_df = pd.DataFrame()
        if soil_meta and soil_meta.get("overburden", {}).get("assigned_areas", 0) > 0:
            try:
                soil_disp_df = _collect_joint_displacements(model, node_names, SOIL_CASE_NAME)

                _log("[DEBUG] SOIL JointDispl rows:", 0 if soil_disp_df is None else len(soil_disp_df))

            except Exception:
                soil_disp_df = pd.DataFrame()

        # --- Shell results (areas) ---
        area_names = _get_all_area_names(model)

        shell_dead_df = pd.DataFrame()
        shell_soil_df = pd.DataFrame()

        if area_names:
            shell_dead_df = _collect_shell_forces_moments(model, area_names, case="Dead")
            if soil:
                shell_soil_df = _collect_shell_forces_moments(model, area_names, case=SOIL_CASE_NAME)

        _log(
            "[ROOTCHECK] Shell SOIL coverage",
            "soil_rows=", 0 if shell_soil_df is None else len(shell_soil_df),
            "unique_areas=", 0 if shell_soil_df is None or shell_soil_df.empty else shell_soil_df["Area"].nunique()
        )

        # ---- Write results workbook ----
        results_xlsx = base + "_results.xlsx"
        _ensure_xlsxwriter()

        MAX_EXCEL_ROWS = 1_048_576
        MAX_EXCEL_DATA_ROWS = MAX_EXCEL_ROWS - 1  # leave 1 row for headers

        def _write_df_excel_only(df, sheet_name, xlw):
            if df is None or df.empty:
                return "skip"

            MAX_EXCEL_ROWS = 1_048_576
            MAX_DATA_ROWS = MAX_EXCEL_ROWS - 1  # header row

            if len(df) <= MAX_DATA_ROWS:
                df.to_excel(xlw, sheet_name=sheet_name, index=False)
                return "excel"

            # Split into chunks across sheets
            start = 0
            part = 1
            while start < len(df):
                end = min(start + MAX_DATA_ROWS, len(df))
                chunk = df.iloc[start:end].copy()
                chunk.to_excel(xlw, sheet_name=f"{sheet_name}_{part}", index=False)
                part += 1
                start = end

            return "excel_split"

        with pd.ExcelWriter(results_xlsx, engine="xlsxwriter") as xlw:
            # ---- Shell extrema summary (M11, V13, Fmax, Fmin) ----
            summary_rows = []
            summary_rows += _extrema_summary(shell_dead_df, "Dead")
            if soil:
                summary_rows += _extrema_summary(shell_soil_df, "Soil")

            if summary_rows:
                pd.DataFrame(summary_rows).to_excel(xlw, sheet_name="ShellExtrema", index=False)

            # --- Settlement at key nodes (mm) ---
            vaxis = (soil.get("axis", "Z") if soil else "Z")
            keys = _pick_key_nodes_for_settlement(nodes, axis=vaxis)

            settle_dead = pd.concat([
                _settlement_mm_from_displacements(disp_df, keys["vertex"],  vertical_axis=vaxis).assign(Group="Vertex"),
                _settlement_mm_from_displacements(disp_df, keys["edges"],   vertical_axis=vaxis).assign(Group="Edges"),
                _settlement_mm_from_displacements(disp_df, keys["corners"], vertical_axis=vaxis).assign(Group="Corners"),
            ], ignore_index=True)

            _write_df_excel_only(settle_dead, "Settlement_Dead_mm", xlw)

            if soil and (soil_disp_df is not None) and (not soil_disp_df.empty):
                settle_soil = pd.concat([
                    _settlement_mm_from_displacements(soil_disp_df, keys["vertex"],  vertical_axis=vaxis).assign(Group="Vertex"),
                    _settlement_mm_from_displacements(soil_disp_df, keys["edges"],   vertical_axis=vaxis).assign(Group="Edges"),
                    _settlement_mm_from_displacements(soil_disp_df, keys["corners"], vertical_axis=vaxis).assign(Group="Corners"),
                ], ignore_index=True)

                _write_df_excel_only(settle_soil, "Settlement_Soil_mm", xlw)

            _write_df_excel_only(nodes, "Nodes", xlw)
            _write_df_excel_only(elems, "Elements", xlw)
            _write_df_excel_only(areas, "Areas", xlw)

            _write_df_excel_only(disp_df, "JointDisplacements", xlw)
            _write_df_excel_only(soil_disp_df, "Soil_JointDisplacements", xlw)
            _write_df_excel_only(force_df, "FrameEndForces", xlw)
            _write_df_excel_only(shell_dead_df, "ShellResults_Dead", xlw)
            _write_df_excel_only(shell_soil_df, "ShellResults_Soil", xlw)

            # Optional: dump soil_meta for debugging
            if soil_meta:
                try:
                    pd.DataFrame([{
                        "E_soil_mpa": float(soil.get("E_soil_mpa", 0.0)),
                        "axis": soil.get("axis", "Z"),
                        "springs_assigned": soil_meta.get("springs", {}).get("assigned", 0),
                        "K_joint_Npm": soil_meta.get("springs", {}).get("K_joint", None),
                        "gamma_Npm3": soil_meta.get("overburden", {}).get("gamma_Npm3", None),
                        "burial_depth_vertex_m": soil_meta.get("overburden", {}).get("burial_depth_vertex_m", None),
                        "z_grade": soil_meta.get("overburden", {}).get("z_grade", None),
                        "z_vertex": soil_meta.get("overburden", {}).get("z_vertex", None),
                        "overburden_assigned_areas": soil_meta.get("overburden", {}).get("assigned_areas", 0),
                        "overburden_pattern": soil_meta.get("overburden", {}).get("pattern", None),
                        "overburden_case": soil_meta.get("overburden", {}).get("case", None),
                    }]).to_excel(xlw, sheet_name="SoilMeta", index=False)
                except Exception:
                    pass

            # Optional: soil sweep outputs
            if soil_results:
                soil_cfg_df = pd.DataFrame([{
                    "depth_min": soil.get("depth_min", 0.0),
                    "depth_max": soil.get("depth_max", 4.0),
                    "depth_step": soil.get("depth_step", 0.5),
                    "gamma (N/m^3)": soil.get("gamma", 18000.0),
                    "axis": soil.get("axis", "Z"),
                    "normalize": soil.get("normalize", False),
                    "load_pattern": soil.get("load_pattern", "SOIL"),
                    "joint_pattern": soil.get("joint_pattern", "SOIL_DEPTH"),
                    "case_name": soil.get("case_name", "SOIL_CASE"),
                    "replace_each_step": soil.get("replace_each_step", True),
                }])
                soil_cfg_df.to_excel(xlw, sheet_name="SoilConfig", index=False)

                depths = soil_results["depths"]
                normalize = soil.get("normalize", False)
                gamma_val = soil.get("gamma", 18000.0)
                summary_rows2 = []
                for d in depths:
                    mult = (gamma_val * d) if normalize else gamma_val
                    summary_rows2.append({"Depth_m": d, "Multiplier": mult})
                pd.DataFrame(summary_rows2).to_excel(xlw, sheet_name="SoilSummary", index=False)

                for depth_val, ddf in zip(soil_results["depths"], soil_results["displacements_by_step"]):
                    if ddf is None or ddf.empty:
                        continue
                    dtag = f"d{str(depth_val).replace('.', '_')}"
                    _write_df_excel_only(ddf, f"SoilDisp_{dtag}", xlw)

        num_areas = 0 if areas is None else len(areas)

        return {
            "model_path": sdb_path,
            "results_path": results_xlsx,
            "num_nodes": len(nodes),
            "num_frames": len(elems),
            "num_areas": num_areas,
            "disp_rows": 0 if disp_df.empty else len(disp_df),
            "force_rows": 0 if force_df.empty else len(force_df),
        }

    finally:
        try:
            if 'sap' in locals() and (close_after or not visible):
                sap.ApplicationExit(True)
        except Exception:
            pass
