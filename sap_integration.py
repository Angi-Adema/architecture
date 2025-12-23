# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements, Areas)
# - Builds model in SAP2000
# - Runs analysis (Dead self-weight, optional soil sweep)
# - Optional soil: stiffness-based base springs + constant overburden pressure (backend-fixed)
# - Exports model + results to <input>_results.xlsx

import importlib
import os
import math
import re
from xml.parsers.expat import model
import pandas as pd
import comtypes.client as cc
import numpy as np

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


# ---- Paths for v20 (adjust if installed elsewhere) ----
DIR_CANDIDATES = [
    r"C:\Program Files\SAP2000 20",
    r"C:\Program Files\Computers and Structures\SAP2000 20",
]


def _as_seq(x, n):
    """Coerce COM return 'x' to a sequence of length n."""
    if n is None:
        n = 0
    try:
        _ = len(x)
        return list(x)
    except Exception:
        return [x] * int(n)


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


def _build_areas_from_excel(model, areas_df,
                            default_section=("SHELL_200", "CONC40", 0.20)):
    """
    Creates area (shell) objects by named joints:
      - Triangles: P1,P2,P3 (P4 is None/blank)
      - Quads:     P1,P2,P3,P4

    Excel 'Areas' sheet columns (no headers):
      0: AreaName
      1: P1   2: P2   3: P3   4: P4(optional)
      5: Section(optional)   6: Material(optional)
    """
    sec_name, mat_name, thick_m = default_section
    area = model.AreaObj

    try:
        model.PropMaterial.SetMaterial(mat_name, 2)
    except Exception:
        pass
    try:
        model.PropArea.SetShell(sec_name, mat_name, float(thick_m))
    except Exception:
        try:
            model.PropArea.SetShell_1(sec_name, mat_name, float(thick_m))
        except Exception:
            pass

    for _, r in areas_df.iterrows():
        name = str(r["Area"])
        p1 = str(r["P1"])
        p2 = str(r["P2"])
        p3 = str(r["P3"])
        p4 = r["P4"]

        sec = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        mat = str(r["Material"]) if pd.notna(r["Material"]) else mat_name

        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, _mat_code_from_name(mat))
            except Exception:
                pass

        try:
            model.PropArea.SetShell(sec, mat, float(thick_m))
        except Exception:
            try:
                model.PropArea.SetShell_1(sec, mat, float(thick_m))
            except Exception:
                pass

        if p4 is None or (isinstance(p4, float) and pd.isna(p4)) or str(p4) == "":
            point_names = [p1, p2, p3]
        else:
            p4 = str(p4)
            point_names = [p1, p2, p3, p4]

        n_pts = len(point_names)

        created_name = name
        created = False
        for meth in ("AddByPoint", "AddByPoint_1", "AddByPoint_2"):
            try:
                m = getattr(area, meth)
            except AttributeError:
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
                except Exception:
                    continue
            except Exception:
                continue

        if not created:
            raise RuntimeError(f"Failed to create area {name} ({','.join(point_names)}) via AddByPoint variants.")

        try:
            area.SetProperty(created_name, sec)
        except Exception:
            pass


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
        return ret
    except Exception as e:
        _log(f"RunAnalysis raised: {e}")
        raise


def _collect_joint_displacements(model, node_names, case="Dead"):
    rows = []
    _select_case(model, case)

    for nname in node_names:
        try:
            ret, nres, Obj, Elm, LoadCase, StepType, StepNum, U1, U2, U3, R1, R2, R3 = \
                model.Results.JointDispl(str(nname), 0, case)
        except Exception:
            continue

        n = int(nres or 0)
        if n == 0:
            continue

        Obj = _as_seq(Obj, n)
        LoadCase = _as_seq(LoadCase, n)
        StepType = _as_seq(StepType, n)
        StepNum = _as_seq(StepNum, n)
        U1 = _as_seq(U1, n)
        U2 = _as_seq(U2, n)
        U3 = _as_seq(U3, n)
        R1 = _as_seq(R1, n)
        R2 = _as_seq(R2, n)
        R3 = _as_seq(R3, n)

        for i in range(n):
            rows.append({
                "Node": Obj[i],
                "Case": LoadCase[i],
                "StepType": StepType[i],
                "StepNum": StepNum[i],
                "UX": U1[i], "UY": U2[i], "UZ": U3[i],
                "RX": R1[i], "RY": R2[i], "RZ": R3[i],
            })
    return pd.DataFrame(rows)


def _collect_frame_end_forces(model, frame_names, case="Dead"):
    rows = []
    _select_case(model, case)

    for fname in frame_names:
        try:
            ret, nres, Obj, Elm, LoadCase, StepType, StepNum, P, V2, V3, T, M2, M3 = \
                model.Results.FrameForce(str(fname), 1, case)  # 1 = ends only
        except Exception:
            continue

        n = int(nres or 0)
        if n == 0:
            continue

        Obj = _as_seq(Obj, n)
        LoadCase = _as_seq(LoadCase, n)
        StepType = _as_seq(StepType, n)
        StepNum = _as_seq(StepNum, n)
        P = _as_seq(P, n)
        V2 = _as_seq(V2, n)
        V3 = _as_seq(V3, n)
        T = _as_seq(T, n)
        M2 = _as_seq(M2, n)
        M3 = _as_seq(M3, n)

        for i in range(n):
            end = "I" if (i % 2 == 0) else "J"
            rows.append({
                "Frame": Obj[i],
                "Case": LoadCase[i],
                "StepType": StepType[i],
                "StepNum": StepNum[i],
                "End": end,
                "P": P[i], "V2": V2[i], "V3": V3[i],
                "T": T[i], "M2": M2[i], "M3": M3[i],
            })
    return pd.DataFrame(rows)

def _collect_shell_forces_moments(model, area_names, case="Dead"):
    """
    Collect shell forces/moments for SAP2000 area objects.
    Expected outputs include:
      F11,F22,F12 (membrane), M11,M22,M12 (bending), V13,V23 (shear)
    Returns a DataFrame with one row per result station.
    """
    rows = []
    _select_case(model, case)

    # Try multiple API variants (different SAP builds expose different names)
    candidates = [
        ("AreaForceShell", lambda a: model.Results.AreaForceShell(str(a), 0, case)),
        ("AreaForceShell_1", lambda a: model.Results.AreaForceShell_1(str(a), 0, case)),
        ("ShellForce", lambda a: model.Results.ShellForce(str(a), 0, case)),
    ]

    def _call(area):
        for name, fn in candidates:
            try:
                return fn(area)
            except Exception:
                continue
        return None

    for an in area_names:
        out = _call(an)
        if not out:
            continue

        try:
            # Typical SAP return tuple shape (varies by build):
            # ret, nres, Obj, Elm, LoadCase, StepType, StepNum, F11, F22, F12, M11, M22, M12, V13, V23
            ret = out[0]
            nres = int(out[1] or 0)
            if nres <= 0:
                continue

            Obj      = _as_seq(out[2], nres)
            Elm      = _as_seq(out[3], nres)  # sometimes station/element id
            LoadCase = _as_seq(out[4], nres)
            StepType = _as_seq(out[5], nres)
            StepNum  = _as_seq(out[6], nres)

            F11 = _as_seq(out[7],  nres)
            F22 = _as_seq(out[8],  nres)
            F12 = _as_seq(out[9],  nres)
            M11 = _as_seq(out[10], nres)
            M22 = _as_seq(out[11], nres)
            M12 = _as_seq(out[12], nres)
            V13 = _as_seq(out[13], nres)
            V23 = _as_seq(out[14], nres)

            for i in range(nres):
                f11 = float(F11[i]); f22 = float(F22[i]); f12 = float(F12[i])

                # Principal membrane forces per unit width:
                avg = 0.5 * (f11 + f22)
                rad = ((0.5 * (f11 - f22)) ** 2 + (f12 ** 2)) ** 0.5
                fmax = avg + rad
                fmin = avg - rad

                rows.append({
                    "Area": Obj[i],
                    "Elm": Elm[i],
                    "Case": LoadCase[i],
                    "StepType": StepType[i],
                    "StepNum": StepNum[i],
                    "F11": f11, "F22": f22, "F12": f12,
                    "Fmax": fmax, "Fmin": fmin,
                    "M11": float(M11[i]), "M22": float(M22[i]), "M12": float(M12[i]),
                    "V13": float(V13[i]), "V23": float(V23[i]),
                })
        except Exception:
            continue

    return pd.DataFrame(rows)

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
    """Returns list of all area (shell) object names."""
    try:
        out = model.AreaObj.GetNameList()
    except Exception:
        return []

    if isinstance(out, tuple):
        if len(out) == 2:
            _, names = out
        elif len(out) == 3:
            _, _, names = out
        else:
            names = out[-1]
    else:
        names = out

    try:
        return list(names)
    except Exception:
        return [names]


def _get_joint_xyz(model, joint_name):
    try:
        _, x, y, z = model.PointObj.GetCoordCartesian(str(joint_name), "Global")
    except Exception:
        _, x, y, z = model.PointObj.GetCoordCartesian(str(joint_name))
    return float(x), float(y), float(z)


def _iter_all_point_coords(model):
    """
    Yield (name, x, y, z) for all joints in the model.
    Handles both variants of GetNameList and GetCoordCartesian.
    """
    try:
        out = model.PointObj.GetNameList()
    except Exception:
        return

    if isinstance(out, (list, tuple)) and len(out) == 3:
        _, _, names = out
    else:
        try:
            _, names = out
        except Exception:
            return

    try:
        names_seq = list(names)
    except Exception:
        names_seq = [names]

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
    try:
        ret, names = model.FrameObj.GetNameList()
    except Exception:
        return []
    try:
        names = list(names)
    except Exception:
        names = [names]

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
    After SAP2000 has built the model, pull out full Nodes/Elements/Areas
    tables so the results workbook matches the actual geometry.
    """
    node_rows = []
    for nm, x, y, z in _iter_all_point_coords(model):
        node_rows.append({"Name": str(nm), "X": x, "Y": y, "Z": z})
    nodes_df = pd.DataFrame(node_rows)

    frame_rows = []
    for nm, i_pt, j_pt, sec in _iter_all_frames(model):
        frame_rows.append({
            "Frame": str(nm),
            "I": str(i_pt) if i_pt is not None else "",
            "J": str(j_pt) if j_pt is not None else "",
            "Section": sec,
            "Material": None,
        })
    elems_df = pd.DataFrame(frame_rows)

    area_rows = []
    for an, pts, sec in _iter_all_areas(model):
        area_rows.append({
            "Area": str(an),
            "P1": pts[0] if len(pts) > 0 else None,
            "P2": pts[1] if len(pts) > 1 else None,
            "P3": pts[2] if len(pts) > 2 else None,
            "P4": pts[3] if len(pts) > 3 else None,
            "Section": sec,
            "Material": None,
        })
    areas_df = pd.DataFrame(area_rows)

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
    Tries SetLoadSurfacePressure first, then SetLoadUniformToPattern as fallback.
    """
    area = model.AreaObj
    try:
        area.SetLoadSurfacePressure(area_name, load_pattern, "Projected",
                                    float(multiplier), coord_sys, bool(replace),
                                    True, joint_pattern)
        return True
    except Exception:
        pass
    try:
        area.SetLoadUniformToPattern(area_name, load_pattern, joint_pattern,
                                     coord_sys, float(multiplier), bool(replace))
        return True
    except Exception:
        pass
    try:
        area.SetLoadUniform(area_name, load_pattern, float(multiplier), coord_sys, bool(replace))
        return False
    except Exception:
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

        force_df = pd.DataFrame()

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
        model.LoadPatterns.Add(load_pattern, 1, 0.0)
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
    joint_pattern_name="SOIL_DEPTH",
    replace=True,
):
    # 1) Ensure load pattern + case exist
    try:
        model.LoadPatterns.Add(load_pattern, 1, 0.0)
    except Exception:
        pass
    try:
        model.LoadCases.StaticLinear.SetCase(case_name)
        model.LoadCases.StaticLinear.SetLoads(case_name, 1, [load_pattern], [1.0])
    except Exception:
        pass

    # 2) Get joints
    joints = list(_iter_all_point_coords(model))
    if not joints:
        return {"assigned_areas": 0, "assigned_joints": 0}

    ax = (vertical_axis or "Z").upper()
    axis_index = 2 if ax == "Z" else (1 if ax == "Y" else 0)

    # 3) Identify vertex elevation as the MAX along vertical axis (vertex should be "up" after flip)
    z_vertex = max((x, y, z)[axis_index] for _, x, y, z in joints)

    # grade elevation in model coords
    z_grade = z_vertex + float(burial_depth_vertex_m)

    # 4) Ensure joint pattern exists, then set joint pattern values = depth (m)
    _ensure_joint_pattern_exists(model, joint_pattern_name)

    assigned_joints = 0
    for joint_name, x, y, z in joints:
        elev = (x, y, z)[axis_index]
        depth_m = max(0.0, z_grade - elev)
        if _set_joint_pattern_value_for_point(model, joint_name, joint_pattern_name, depth_m):
            assigned_joints += 1

    # 5) Assign surface pressure "By Joint Pattern" to all areas with multiplier = gamma
    area_names = _get_all_area_names(model)
    assigned_areas = 0
    for an in area_names:
        ok = _assign_area_surface_pressure_by_joint_pattern(
            model,
            area_name=str(an),
            load_pattern=load_pattern,
            joint_pattern=joint_pattern_name,
            multiplier=float(gamma_soil_Npm3),
            coord_sys="Global",
            replace=bool(replace)
        )
        if ok:
            assigned_areas += 1

    return {
        "assigned_areas": assigned_areas,
        "assigned_joints": assigned_joints,
        "gamma_Npm3": float(gamma_soil_Npm3),
        "burial_depth_vertex_m": float(burial_depth_vertex_m),
        "z_vertex": float(z_vertex),
        "z_grade": float(z_grade),
        "pattern": load_pattern,
        "case": case_name,
        "joint_pattern": joint_pattern_name
    }

# def _assign_constant_overburden_to_base_areas(
#     model,
#     nodes_df,
#     areas_df,
#     pressure_Npm2=OVERBURDEN_PRESSURE_NPM2,
#     load_pattern=SOIL_LOAD_PATTERN,
#     case_name=SOIL_CASE_NAME,
#     vertical_axis="Z"
# ):
#     """
#     Assign a constant surface pressure to areas that lie on the base (min Z).
#     Pressure is in N/m^2 (Pa) for N-m units.

#     NOTE on sign:
#       - If you want downward pressure, use +pressure
#       - If you want upward (soil reaction), use -pressure
#     """
#     if areas_df is None or areas_df.empty:
#         return {"assigned_areas": 0}

#     try:
#         model.LoadPatterns.Add(load_pattern, 1, 0.0)
#     except Exception:
#         pass

#     try:
#         model.LoadCases.StaticLinear.SetCase(case_name)
#         model.LoadCases.StaticLinear.SetLoads(case_name, 1, [load_pattern], [1.0])
#     except Exception:
#         pass

#     zmin = float(nodes_df["Z"].min())
#     node_z = dict(zip(nodes_df["Name"].astype(str), nodes_df["Z"].astype(float)))

#     assigned = 0
#     for _, r in areas_df.iterrows():
#         an = str(r["Area"])
#         pts = [r.get("P1"), r.get("P2"), r.get("P3"), r.get("P4")]
#         pts = [str(p) for p in pts if p is not None and str(p).strip() != ""]
#         if len(pts) < 3:
#             continue
        
#         TOL_Z = 1e-3
#         if all(abs(node_z.get(p, 1e9) - zmin) <= TOL_Z for p in pts):
#             try:
#                 model.AreaObj.SetLoadSurfacePressure(
#                     an, load_pattern, "Projected",
#                     float(pressure_Npm2), "Global", True
#                 )
#                 assigned += 1
#             except Exception:
#                 pass

#     return {"assigned_areas": assigned, "pressure": pressure_Npm2, "pattern": load_pattern, "case": case_name}

def _ensure_case_runs_in_analysis(model, case_name):
    """
    Some SAP2000 COM builds do not automatically include newly-created cases
    in the analysis run set. This forces the case to be runnable if the API supports it.
    """
    try:
        analyze = model.Analyze

        # Most common signature on many builds:
        # SetRunCaseFlag(CaseName, Run, All)
        try:
            analyze.SetRunCaseFlag(str(case_name), True, True)
            return True
        except Exception:
            pass

        # Variant found on some installs:
        try:
            analyze.SetRunCaseFlag_1(str(case_name), True, True)
            return True
        except Exception:
            pass

        # If no method exists, we can't force it here (we'll rely on re-run analysis).
        return False

    except Exception:
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

    _ensure_openpyxl()

    # ---- read Excel ----
    nodes_in = _read_nodes_sheet(input_xlsx)
    elems_in = _read_elements_sheet(input_xlsx)

    _log(
        "Bounds:",
        f"X [{nodes_in['X'].min()}, {nodes_in['X'].max()}], "
        f"Y [{nodes_in['Y'].min()}, {nodes_in['Y'].max()}], "
        f"Z [{nodes_in['Z'].min()}, {nodes_in['Z'].max()}]"
    )

    if nodes_in.empty:
        raise ValueError("Nodes sheet is empty.")

    try:
        areas_in = _read_areas_sheet(input_xlsx)

        _log("[DEBUG] areas_in rows:", 0 if areas_in is None else len(areas_in))

        if areas_in is not None and areas_in.empty:
            areas_in = None
    except Exception:
        areas_in = None

    _ = _infer_ne_from_filename(input_xlsx) or 4  # kept for compatibility if you use later

    sap, model = _start_sap2000_v20(visible=visible)
    try:
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

            # Orient area local axes based on node labels (1 and E+3), fallback to (1,23)
            try:
                nodes_tot = len(nodes_in)
                E = int(round(nodes_tot ** 0.5)) - 1
                behind_name = str(E + 3)
                _set_area_axes_by_two_joints(model, joint1="1", joint2=behind_name, plane="31", replace=True)
            except Exception:
                _set_area_axes_by_two_joints(model, joint1="1", joint2="23", plane="31", replace=True)

        # Extract the actual built model
        nodes, elems, areas = _extract_model_to_dfs(model)

        # Supports + Dead load
        if soil:
            # Base restraints for soil-spring model:
            # Fix UX, UY; leave UZ free so the vertical spring can act
            _fix_base_nodes(model, nodes, fix=(1, 1, 0, 1, 1, 1))
        else:
            # No-soil model: fully fixed base
            _fix_base_nodes(model, nodes)

        _add_default_self_weight(model, "Dead", 1.0)

        
        # Soil actions: stiffness-based support + constant overburden
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
                    joint_pattern_name="SOIL_DEPTH",
                    replace=True
                )

                soil_meta = {"springs": springs_meta, "overburden": overburden_meta}

                # Force SOIL_CASE into the analysis run set (prevents "loads exist but case never ran")
                _ensure_case_runs_in_analysis(model, SOIL_CASE_NAME)

                soil_meta = {"springs": springs_meta, "overburden": overburden_meta}

                _log("[Soil]", "Springs:", springs_meta, "Overburden:", overburden_meta)
            except Exception as e:
                _log("[Soil] Skipped:", e)

        # Save model beside spreadsheet
        base, _ext = os.path.splitext(input_xlsx)
        sdb_path = base + ".sdb"
        try:
            model.File.Save(sdb_path)
        except Exception:
            pass
        
        # Force cases into run set
        _ensure_case_runs_in_analysis(model, "Dead")
        if soil:
            _ensure_case_runs_in_analysis(model, SOIL_CASE_NAME)
    
        # Run analysis once (Dead + any soil case that exists)
        _run_analysis(model)

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

        # Collect results
        node_names = nodes["Name"].astype(str).tolist()
        frame_names = elems["Frame"].astype(str).tolist() if ("Frame" in elems.columns and len(elems) > 0) else []

        import traceback
        try:
            disp_df = _collect_joint_displacements(model, node_names, "Dead")
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

        # ---- Shell extrema summary (M11, V13, Fmax, Fmin) ----
        summary_rows = []
        summary_rows += _extrema_summary(shell_dead_df, "Dead")
        if soil:
            summary_rows += _extrema_summary(shell_soil_df, "Soil")

        if summary_rows:
            pd.DataFrame(summary_rows).to_excel(xlw, sheet_name="ShellExtrema", index=False)

        # --- Settlement at key nodes (mm) ---
        keys = _pick_key_nodes_for_settlement(nodes, axis=soil.get("axis","Z") if soil else "Z")
        settle_dead = pd.concat([
            _settlement_mm_from_displacements(disp_df, keys["vertex"], vertical_axis=soil.get("axis","Z") if soil else "Z").assign(Group="Vertex"),
            _settlement_mm_from_displacements(disp_df, keys["edges"],  vertical_axis=soil.get("axis","Z") if soil else "Z").assign(Group="Edges"),
            _settlement_mm_from_displacements(disp_df, keys["corners"],vertical_axis=soil.get("axis","Z") if soil else "Z").assign(Group="Corners"),
        ], ignore_index=True)
        settle_soil = pd.DataFrame()
        if soil and soil_disp_df is not None and not soil_disp_df.empty:
            settle_soil = pd.concat([
                _settlement_mm_from_displacements(soil_disp_df, keys["vertex"], vertical_axis=soil.get("axis","Z")).assign(Group="Vertex"),
                _settlement_mm_from_displacements(soil_disp_df, keys["edges"],  vertical_axis=soil.get("axis","Z")).assign(Group="Edges"),
                _settlement_mm_from_displacements(soil_disp_df, keys["corners"],vertical_axis=soil.get("axis","Z")).assign(Group="Corners"),
            ], ignore_index=True)

        # Write results workbook
        results_xlsx = base + "_results.xlsx"
        _ensure_xlsxwriter()

        with pd.ExcelWriter(results_xlsx, engine="xlsxwriter") as xlw:
            nodes.to_excel(xlw, sheet_name="Nodes", index=False)
            elems.to_excel(xlw, sheet_name="Elements", index=False)
            if areas is not None and not areas.empty:
                areas.to_excel(xlw, sheet_name="Areas", index=False)

            if not disp_df.empty:
                disp_df.to_excel(xlw, sheet_name="JointDisplacements", index=False)
            if not soil_disp_df.empty:
                soil_disp_df.to_excel(xlw, sheet_name="Soil_JointDisplacements", index=False)
            if not force_df.empty:
                force_df.to_excel(xlw, sheet_name="FrameEndForces", index=False)
            if not shell_dead_df.empty:
                shell_dead_df.to_excel(xlw, sheet_name="ShellResults_Dead", index=False)
            if soil and not shell_soil_df.empty:
                shell_soil_df.to_excel(xlw, sheet_name="ShellResults_Soil", index=False)
            if not settle_dead.empty:
                settle_dead.to_excel(xlw, sheet_name="Settlement_Dead_mm", index=False)
            if soil and not settle_soil.empty:
                settle_soil.to_excel(xlw, sheet_name="Settlement_Soil_mm", index=False)

            # Optional: dump soil_meta for debugging
            if soil_meta:
                try:
                    pd.DataFrame([{
                        "E_soil_mpa": float(soil.get("E_soil_mpa", 0.0)),
                        "axis": soil.get("axis", "Z"),
                        "springs_assigned": soil_meta.get("springs", {}).get("assigned", 0),
                        "K_joint_Npm": soil_meta.get("springs", {}).get("K_joint", None),
                        "overburden_pressure_Npm2": soil_meta.get("overburden", {}).get("pressure", None),
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
                summary_rows = []
                for d in depths:
                    mult = (gamma_val * d) if normalize else gamma_val
                    summary_rows.append({"Depth_m": d, "Multiplier": mult})
                pd.DataFrame(summary_rows).to_excel(xlw, sheet_name="SoilSummary", index=False)

                for depth_val, ddf in zip(soil_results["depths"], soil_results["displacements_by_step"]):
                    if ddf.empty:
                        continue
                    dtag = f"d{str(depth_val).replace('.', '_')}"
                    ddf.to_excel(xlw, sheet_name=f"SoilDisp_{dtag}", index=False)

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
