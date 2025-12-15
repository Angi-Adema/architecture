# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements)
# - Builds model in SAP2000 (one tympan)
# - Uses SAP2000 radial replicate to build full umbrella
# - Runs analysis (Dead self-weight, optional soil sweep)
# - Exports model + results to <input>_results.xlsx

import importlib
import os
import math
import re
import pandas as pd
import comtypes.client as cc

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
    # common header patterns
    if ("x" in first_row and "y" in first_row and "z" in first_row) or ("name" in first_row):
        df = df.iloc[1:].reset_index(drop=True)

    # Now proceed with your existing column detection logic
    # (assuming Name, X, Y, Z are the first 4 columns)
    name_col, x_col, y_col, z_col = 0, 1, 2, 3

    out = pd.DataFrame({
        "Name": df.iloc[:, name_col].astype(str).str.strip(),
        "X": df.iloc[:, x_col].astype(float),
        "Y": df.iloc[:, y_col].astype(float),
        "Z": df.iloc[:, z_col].astype(float),
    })

    # Optionally drop blank names
    out = out[out["Name"].notna() & (out["Name"] != "")]
    return out


def _read_elements_sheet(xlsx_path):
    """
    'Elements' without headers, first 5 columns:
      0 Frame, 1 I, 2 J, 3 Section (opt), 4 Material (opt)
    """
    df = pd.read_excel(xlsx_path, sheet_name="Elements", header=None)
    for c in range(df.shape[1], 5):
        df[c] = None
    return df.rename(columns={0: "Frame", 1: "I", 2: "J", 3: "Section", 4: "Material"})[
        ["Frame", "I", "J", "Section", "Material"]
    ]


def _read_areas_sheet(xlsx_path):
    """
    Reads 'Areas' sheet (no headers). Supports triangles (3 points) or quads (4 points).
    Column layout (no header):
      0: AreaName
      1: P1   2: P2   3: P3   4: P4(optional, can be blank/NaN)
      5: Section(optional)   6: Material(optional)

    Returns a normalized DataFrame with columns:
      Area, P1, P2, P3, P4, Section, Material
    """
    df = pd.read_excel(xlsx_path, sheet_name="Areas", header=None)
    for c in range(df.shape[1], 7):
        df[c] = None
    df = df.rename(columns={
        0: "Area", 1: "P1", 2: "P2", 3: "P3", 4: "P4", 5: "Section", 6: "Material"
    })[["Area", "P1", "P2", "P3", "P4", "Section", "Material"]]
    # normalize blanks
    df["P4"] = df["P4"].where(pd.notna(df["P4"]) & (df["P4"].astype(str).str.len() > 0), None)
    return df


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
        pass  # ok if already exists


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
    # SAP codes: 1=Steel, 2=Concrete (common in v20)
    mat_code = 2 if mtype == "concrete" else 1

    # Define material (handle different API variants)
    try:
        model.PropMaterial.SetMaterial(name, mat_code)
    except Exception:
        try:
            model.PropMaterial.SetMaterial_1(name, region, "", "")
        except Exception:
            pass  # ok if it already exists

    # Convert inputs assuming model units = N-m-C (as set in _start_sap2000_v20)
    E_pa = float(spec.get("E", 30e9))         # Pa (= N/m^2)
    E_kPa = E_pa / 1000.0                     # kN/m^2 if we ever switch units
    nu = float(spec.get("nu", 0.2))
    alpha = float(spec.get("alpha", 1.0e-5))  # 1/C

    try:
        model.PropMaterial.SetMPIsotropic(name, E_kPa, nu, alpha)
    except Exception:
        try:
            model.PropMaterial.SetMPIsotropic_1(name, E_kPa, nu, alpha)
        except Exception:
            pass

    # Unit weight & mass density
    gamma_Npm3 = float(spec.get("gamma", 24000.0))  # N/m^3
    gamma_kNpm3 = gamma_Npm3 / 1000.0               # kN/m^3
    mass_kN_s2_m4 = gamma_kNpm3 / 9.80665           # consistent mass density
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

def _create_points_from_nodes(model, nodes_df):
    """
    Create SAP2000 joints from the Nodes dataframe only
    (no frames). Columns: Name, X, Y, Z
    """
    for _, r in nodes_df.iterrows():
        name = str(r["Name"])
        x = float(r["X"])
        y = float(r["Y"])
        z = float(r["Z"])
        try:
            # AddCartesian(x, y, z, UserName)
            model.PointObj.AddCartesian(x, y, z, name)
        except Exception:
            # If the point already exists with this name, just keep going
            pass


def _build_model_from_excel(model, nodes_df, elems_df,
                            default_section=("RECT_300x500", "CONC40", (0.30, 0.50))):
    """
    Create points and frames in SAP2000 from the provided dataframes.

    nodes_df columns: Name, X, Y, Z
    elems_df columns: Frame, I, J, Section (opt), Material (opt)
    """
    sec_name, mat_name, dims = default_section
    _ensure_default_section(model, sec_name, mat_name, dims)

    # -- helper: robust existence check for a point across API variants
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

    # ----- Points -----
    for _, r in nodes_df.iterrows():
        name, x, y, z = r["Name"], r["X"], r["Y"], r["Z"]
        try:
            model.PointObj.AddCartesian(float(x), float(y), float(z), str(name))
        except Exception:
            # Likely duplicate; safe to continue
            pass

    # ----- Frames -----
    b, h = map(float, dims)
    for _, r in elems_df.iterrows():
        fname = str(r["Frame"]).strip()
        i_pt = str(r["I"]).strip()
        j_pt = str(r["J"]).strip()

        raw_sec = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        raw_mat = str(r["Material"]) if pd.notna(r["Material"]) else mat_name
        sec = (raw_sec or "").strip() or sec_name
        mat = (raw_mat or "").strip() or mat_name

        # Guards
        if not fname:
            raise RuntimeError(f"Elements sheet has a frame with empty name (I={i_pt}, J={j_pt}).")
        if i_pt == j_pt:
            raise RuntimeError(f"Frame '{fname}' has identical I and J points: {i_pt}.")

        # Ensure material if explicitly provided in the sheet
        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, _mat_code_from_name(mat))  # 1=Steel, 2=Concrete
            except Exception:
                try:
                    model.PropMaterial.SetMaterial_1(mat, "User", "", "")
                except Exception:
                    pass  # fine if already defined

        # Ensure the rectangular section exists (b, h in meters)
        try:
            model.PropFrame.SetRectangle(sec, mat, b, h)
        except Exception:
            pass  # fine if already exists

        # Replace-if-exists behavior to avoid AddByPoint failing on name collision
        try:
            model.FrameObj.Delete(fname)
        except Exception:
            pass

        # Points must exist
        if not _point_exists(i_pt) or not _point_exists(j_pt):
            raise RuntimeError(
                f"Point '{i_pt}' or '{j_pt}' not found before creating frame '{fname}'."
            )

        # Create frame: Name=fname, PropName=sec, CSys="Global"
        try:
            model.FrameObj.AddByPoint(i_pt, j_pt, fname, sec, "Global")
        except Exception as e:
            raise RuntimeError(f"Failed to create frame {fname} ({i_pt}->{j_pt}): {e}")

        # Ensure property applied (covers odd builds)
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

    # Ensure a default shell property & material exist
    try:
        model.PropMaterial.SetMaterial(mat_name, 2)  # 2 = Concrete (SAP code)
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

        # Section / Material (optional overrides)
        sec = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        mat = str(r["Material"]) if pd.notna(r["Material"]) else mat_name

        # (Re)define material if custom
        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, _mat_code_from_name(mat))
            except Exception:
                pass

        # (Re)define shell property for this section
        try:
            model.PropArea.SetShell(sec, mat, float(thick_m))
        except Exception:
            try:
                model.PropArea.SetShell_1(sec, mat, float(thick_m))
            except Exception:
                pass

        # Build point list
        if p4 is None or (isinstance(p4, float) and pd.isna(p4)) or str(p4) == "":
            # Triangle
            point_names = [p1, p2, p3]
        else:
            # Quad
            p4 = str(p4)
            point_names = [p1, p2, p3, p4]

        n_pts = len(point_names)

        # Try several AddByPoint signatures (version-safe)
        created_name = name
        created = False
        for meth in ("AddByPoint", "AddByPoint_1", "AddByPoint_2"):
            try:
                m = getattr(area, meth)
            except AttributeError:
                continue

            try:
                # Most common: (NumberPoints, PointNames, Name, PropName, CSys)
                ret = m(n_pts, point_names, created_name, sec, "Global")
                created = True
                break
            except TypeError:
                try:
                    # Some builds: (NumberPoints, PointNames, Name)
                    ret = m(n_pts, point_names, created_name)
                    created = True
                    break
                except Exception:
                    continue
            except Exception:
                continue

        if not created:
            raise RuntimeError(
                f"Failed to create area {name} "
                f"({','.join(point_names)}) via AddByPoint variants."
            )

        # Try to assign property explicitly (in case the signature didn’t)
        try:
            area.SetProperty(created_name, sec)
        except Exception:
            pass


def _fix_base_nodes(model, nodes_df, tol=1e-6, fix=(1, 1, 1, 1, 1, 1)):
    """
    Fix nodes at the minimum Z (within tol).
    fix: tuple/list of 6 flags (UX, UY, UZ, RX, RY, RZ).
        e.g. fixed: (1,1,1,1,1,1), pinned: (1,1,1,0,0,0)
    """
    zmin = float(nodes_df["Z"].min())
    base = nodes_df.loc[(nodes_df["Z"] - zmin).abs() <= tol, "Name"].astype(str).tolist()

    # Ensure we pass a single SAFEARRAY/sequence, not six separate args
    restr = list(fix)  # comtypes will marshal this to SAFEARRAY(VARIANT_BOOL/INT)
    for n in base:
        # v20 wants: SetRestraint(Name, Restraint[6])
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
        # v20 signature:
        # (ret, NumberResults, Obj, Elm, LoadCase, StepType, StepNum, U1, U2, U3, R1, R2, R3)
        try:
            ret, nres, Obj, Elm, LoadCase, StepType, StepNum, U1, U2, U3, R1, R2, R3 = \
                model.Results.JointDispl(str(nname), 0, case)
        except Exception:
            # skip bad node gracefully
            continue

        _log(
            f"[JointDispl] node={nname!r} -> nres={nres} | "
            f"types: Obj={type(Obj).__name__}, U1={type(U1).__name__}, "
            f"LoadCase={type(LoadCase).__name__}, StepType={type(StepType).__name__}, "
            f"StepNum={type(StepNum).__name__}"
        )

        n = int(nres or 0)
        if n == 0:
            continue

        # Coerce possible scalars into sequences of length n
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
        # v20 signature:
        # (ret, NumberResults, Obj, Elm, LoadCase, StepType, StepNum, P, V2, V3, T, M2, M3)
        try:
            ret, nres, Obj, Elm, LoadCase, StepType, StepNum, P, V2, V3, T, M2, M3 = \
                model.Results.FrameForce(str(fname), 1, case)  # 1 = ends only
        except Exception:
            continue

        _log(
            f"[FrameForce] frame={fname!r} -> nres={nres} | "
            f"types: Obj={type(Obj).__name__}, P={type(P).__name__}, "
            f"LoadCase={type(LoadCase).__name__}, StepType={type(StepType).__name__}, "
            f"StepNum={type(StepNum).__name__}"
        )

        n = int(nres or 0)
        if n == 0:
            continue

        # Normalize to sequences
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
            # result order comes I/J alternating for ends-only
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


# ---------------- Area & Pattern Automation ---------------- #

def _get_all_area_names(model):
    """
    Returns list of all area (shell) object names.

    Handles both (ret, names) and (ret, count, names) signatures.
    """
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


def _autodetect_orientation_joints(model, vertical_axis="Z"):
    """
    Returns (apex_name, behind_name) using the "max along vertical axis"
    rule + nearest in horizontal plane.
    """
    ax = vertical_axis.upper()
    if ax not in ("X", "Y", "Z"):
        ax = "Z"

    # Gather all joints with coordinates
    joints = list(_iter_all_point_coords(model))
    if not joints:
        return None, None

    # Choose index and horizontal components
    if ax == "Z":
        axis_index = 2
        horiz_idx = (0, 1)  # X, Y
    elif ax == "Y":
        axis_index = 1
        horiz_idx = (0, 2)  # X, Z
    else:  # "X"
        axis_index = 0
        horiz_idx = (1, 2)  # Y, Z

    # Apex = max along vertical axis
    apex = max(joints, key=lambda t: (t[1], t[2], t[3])[axis_index])  # (name,x,y,z); using tuple view
    apex_name, ax_x, ax_y, ax_z = apex
    apex_vert = (ax_x, ax_y, ax_z)[axis_index]
    ax_h1 = (ax_x, ax_y, ax_z)[horiz_idx[0]]
    ax_h2 = (ax_x, ax_y, ax_z)[horiz_idx[1]]

    # Among joints strictly below apex along vertical axis,
    # pick the one closest in the horizontal plane.
    candidates = []
    for nm, x, y, z in joints:
        if nm == apex_name:
            continue
        vert = (x, y, z)[axis_index]
        if vert >= apex_vert - 1e-12:
            continue  # must be "behind" (lower) than apex
        h1 = (x, y, z)[horiz_idx[0]]
        h2 = (x, y, z)[horiz_idx[1]]
        dh = math.hypot(h1 - ax_h1, h2 - ax_h2)
        candidates.append((dh, nm))

    if not candidates:
        # Fallback: second-highest along vertical axis
        others = [t for t in joints if t[0] != apex_name]
        if not others:
            return apex_name, None
        behind_name = max(others, key=lambda t: (t[1], t[2], t[3])[axis_index])[0]
        return apex_name, behind_name

    behind_name = min(candidates)[1]
    return apex_name, behind_name


def _rename_point(model, old_name, new_name):
    """
    Renames a point to new_name, handling the case where new_name already exists.
    """
    if not old_name or not new_name or old_name == new_name:
        return new_name

    try:
        # If target doesn't exist, simple rename
        model.PointObj.ChangeName(str(old_name), str(new_name))
        return new_name
    except Exception:
        # If new_name exists, swap via a temporary name
        tmp = f"{new_name}__tmp__"
        try:
            model.PointObj.ChangeName(str(new_name), tmp)
        except Exception:
            pass
        try:
            model.PointObj.ChangeName(str(old_name), str(new_name))
            # try to clean up tmp -> old_name (optional)
            try:
                model.PointObj.ChangeName(tmp, str(old_name))
            except Exception:
                pass
            return new_name
        except Exception:
            # If all else fails, keep old_name
            return old_name


def _set_area_axes_by_two_joints(model, joint1="1", joint2="23", plane="31", replace=True):
    """
    Emulates UI: Assign > Area > Local Axes... > Specify Advanced Local Axes
    Plane ≈ 3-1, Two Joints = (joint1, joint2).
    Tries advanced API variants, falls back to a 2D rotation (in horizontal plane).
    """
    area = model.AreaObj
    names = _get_all_area_names(model)
    if not names:
        return

    for an in names:
        # 1) Try advanced variants first
        for meth in ("SetLocalAxesAdvanced", "SetLocalAxes_2", "SetLocalAxesByPoints"):
            try:
                m = getattr(area, meth)
            except AttributeError:
                continue

            try:
                plane_code = 3  # maps to "Plane 3-1" on many v20 builds
                if meth == "SetLocalAxesAdvanced":
                    axis_dir = 1  # define local-1 by the vector (j1->j2)
                    m(an, plane_code, axis_dir, True, str(joint1), str(joint2), bool(replace))
                elif meth == "SetLocalAxesByPoints":
                    m(an, plane_code, str(joint1), str(joint2), bool(replace))
                else:
                    axis_dir = 1
                    m(an, plane_code, axis_dir, True, str(joint1), str(joint2), bool(replace))
                break  # done for this area
            except Exception:
                continue
        else:
            # 2) Fallback: rotate axes to align with projection of j1->j2 in XY
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


def _ensure_joint_pattern_exists(model, pattern_name="SOIL_DEPTH"):
    """
    Ensures a joint pattern with 'pattern_name' exists.
    """
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
    """
    Set joint pattern value at a specific point (joint).
    """
    point = model.PointObj
    method_names = ["SetPatternValue", "SetPattern", "SetPatternValue_1"]
    for m in method_names:
        try:
            getattr(point, m)(str(joint_name), str(pattern_name), float(value))
            return True
        except Exception:
            continue
    return False


def _iter_all_point_coords(model):
    """
    Yield (name, x, y, z) for all joints in the model.

    Handles both SAP2000 variants of GetNameList:
      - (ret, NumberNames, Names)
      - (ret, Names)

    And both variants of GetCoordCartesian:
      - (ret, x, y, z)
      - (ret, x, y, z, CSys)

    Always casts joint names to strings so COM doesn’t see ints.
    """
    try:
        out = model.PointObj.GetNameList()
    except Exception:
        return  # nothing to yield

    # Normalize GetNameList outputs
    # out might be (ret, n, names) OR (ret, names)
    if isinstance(out, (list, tuple)) and len(out) == 3:
        ret, n_names, names = out
    else:
        try:
            ret, names = out
        except Exception:
            # Unexpected shape – bail out quietly
            return

    # Make sure we have a Python sequence of names
    try:
        names_seq = list(names)
    except Exception:
        names_seq = [names]

    for nm in names_seq:
        name_str = str(nm)  # ALWAYS pass a string into COM

        # Try with CSys argument first, fall back to older signature
        try:
            out_coord = model.PointObj.GetCoordCartesian(name_str, "Global")
        except Exception:
            out_coord = model.PointObj.GetCoordCartesian(name_str)

        # out_coord can be (ret,x,y,z) or (ret,x,y,z,csys)
        if isinstance(out_coord, (list, tuple)) and len(out_coord) >= 4:
            _, x, y, z = out_coord[:4]
        else:
            # If something really odd comes back, skip this point
            continue

        yield name_str, float(x), float(y), float(z)


# --- new helpers for group, replication, and extracting full model --- #

def _infer_ne_from_filename(xlsx_path):
    """
    Try to infer the number of tympans (Ne) from the input filename.
    Expected pattern from umbrella.py:  Hypar4_H2.0_R1.0_N20.xlsx
                                      ^^^^^
    Returns int or None.
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


def _make_tympan_group_from_all(model, group_name="Tympan"):
    """
    Create a group containing all current objects (points, frames, areas).
    Mirrors the Ctrl+A + Assign > Assign to Group flow.
    """
    # Clear if it exists
    try:
        model.Groups.Delete(group_name)
    except Exception:
        pass

    # Points
    try:
        ret, point_names = model.PointObj.GetNameList()
        try:
            point_names = list(point_names)
        except Exception:
            point_names = [point_names]
        for nm in point_names:
            try:
                model.GroupDef.SetGroupAssign("Point", nm, group_name)
            except Exception:
                try:
                    model.Groups.AddJoint(nm, group_name)
                except Exception:
                    pass
    except Exception:
        pass

    # Frames
    try:
        ret, frame_names = model.FrameObj.GetNameList()
        try:
            frame_names = list(frame_names)
        except Exception:
            frame_names = [frame_names]
        for nm in frame_names:
            try:
                model.GroupDef.SetGroupAssign("Frame", nm, group_name)
            except Exception:
                try:
                    model.Groups.AddFrame(nm, group_name)
                except Exception:
                    pass
    except Exception:
        pass

    # Areas
    try:
        names = _get_all_area_names(model)
        for nm in names:
            try:
                model.GroupDef.SetGroupAssign("Area", nm, group_name)
            except Exception:
                try:
                    model.Groups.AddArea(nm, group_name)
                except Exception:
                    pass
    except Exception:
        pass


def _replicate_group_radially(
    model,
    group_name="Tympan",
    n_copies=3,
    angle_deg=90.0,
    axis="Z",
    c_sys="Global",
    delete_original=False,
):
    """
    Emulate Edit > Replicate > Radial for a whole group.
    This uses EditGeneral.EditReplicateRadial on the specified group if available.
    """
    if n_copies <= 0:
        return

    axis = axis.upper()

    # Prefer the whole-group radial replicate if available (simplest & closest
    # to the UI that already works for you).
    try:
        eg = getattr(model, "EditGeneral")
        try:
            eg.EditReplicateRadial(
                1,                 # NumberItems (ignored when using GroupName)
                0,                 # ObjectType (ignored)
                "",                # ObjectName (ignored)
                int(n_copies),
                0.0, 0.0, 0.0,     # origin at (0,0,0)
                float(angle_deg),  # RotationAngle per copy
                str(c_sys),
                True,              # IsRadial
                bool(delete_original),
                str(group_name),
            )
            return
        except Exception:
            pass
    except Exception:
        pass

    # Fallback: if we can't do radial replicate through the API,
    # we leave a single tympan but keep a valid model.
    _log("[WARN] Could not perform radial replication via API; leaving single tympan.")


def _iter_all_frames(model):
    """
    Yield (name, I, J, Section) for all frame objects.
    """
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
    """
    Yield (name, [points...], section) for all area objects.
    """
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
    After SAP2000 has built (and possibly replicated) the model, pull out
    full Nodes/Elements/Areas tables so the results workbook matches the
    actual umbrella geometry.
    """
    # Nodes
    node_rows = []
    for nm, x, y, z in _iter_all_point_coords(model):
        node_rows.append({"Name": str(nm), "X": x, "Y": y, "Z": z})
    nodes_df = pd.DataFrame(node_rows)

    # Frames
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

    # Areas
    area_rows = []
    for an, pts, sec in _iter_all_areas(model):
        row = {
            "Area": str(an),
            "P1": pts[0] if len(pts) > 0 else None,
            "P2": pts[1] if len(pts) > 1 else None,
            "P3": pts[2] if len(pts) > 2 else None,
            "P4": pts[3] if len(pts) > 3 else None,
            "Section": sec,
            "Material": None,
        }
        area_rows.append(row)
    areas_df = pd.DataFrame(area_rows)

    return nodes_df, elems_df, areas_df


# ---------------- Soil / pattern automation ---------------- #

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
    # ---- Input guards ----
    if depth_step <= 0:
        raise ValueError("depth_step must be > 0")
    if depth_min > depth_max:
        depth_min, depth_max = depth_max, depth_min  # swap defensively

    # Ensure load pattern and joint pattern exist
    try:
        model.LoadPatterns.Add(load_pattern_name, 1, 0.0)  # 1 = Dead (type), self-weight=0 for SOIL
    except Exception:
        pass
    _ensure_joint_pattern_exists(model, joint_pattern_name)

    # Ensure we have areas and joints
    area_names = _get_all_area_names(model)
    if not area_names:
        raise RuntimeError("No area (shell) objects found. Soil pressure must be assigned to areas.")

    joints = list(_iter_all_point_coords(model))
    if not joints:
        raise RuntimeError("No joints found in model to set joint-pattern values.")

    # Vertical axis + surface elevation
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

    # Define / configure a static case that uses the soil load
    try:
        model.LoadCases.StaticLinear.SetCase(results_case_name)
        model.LoadCases.StaticLinear.SetLoads(results_case_name, 1, [load_pattern_name], [1.0])
    except Exception:
        pass

    results = []
    d = float(depth_min)

    # Sweep from min to max (with a small epsilon for floating accumulation)
    while d <= depth_max + 1e-9:
        d = round(d, 6)  # keep sheet/tab names clean and stable

        # Compute multiplier ONCE per step
        if normalize_pattern:
            multiplier = gamma_soil_N_per_m3 * d  # N/m^2
        else:
            multiplier = gamma_soil_N_per_m3       # N/m^3

        # Set joint pattern values for this step depth d
        for joint_name, x, y, z in joints:
            elev = (x, y, z)[axis_index]
            raw_depth = max(0.0, surface_elev - elev)
            depth_for_step = min(raw_depth, d)

            if normalize_pattern:
                pattern_value = 0.0 if d <= 1e-12 else (depth_for_step / d)  # 0..1
            else:
                pattern_value = depth_for_step  # meters

            _set_joint_pattern_value_for_point(
                model, joint_name, joint_pattern_name, pattern_value
            )

        # Assign pressure to all areas via the joint pattern
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

        # Solve and collect
        _run_analysis(model)

        node_names = [n for (n, _, _, _) in joints]
        try:
            disp_df = _collect_joint_displacements(model, node_names, case=results_case_name)
        except Exception:
            disp_df = _collect_joint_displacements(model, node_names, case="Dead")

        force_df = pd.DataFrame()  # add frame/area forces here later if desired

        # Tag the outputs with the step depth and multiplier used
        disp_df.insert(0, "Depth_m", d)
        disp_df.insert(1, "Multiplier", multiplier)
        if not force_df.empty:
            force_df.insert(0, "Depth_m", d)
            force_df.insert(1, "Multiplier", multiplier)

        results.append((d, disp_df, force_df))
        d = round(d + depth_step, 6)

    # Optional: write a dedicated results book
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


def run_sap2000_analysis(input_xlsx, visible=True, close_after=False, soil=None, material=None):
    """
    Build & analyze a model from 'Nodes', optional 'Elements', and optional 'Areas' sheets,
    then write results to: <input_basename>_results.xlsx

    Frames (line objects) are now optional – a pure shell model is allowed.
    """
    if not os.path.exists(input_xlsx):
        raise FileNotFoundError(input_xlsx)

    _ensure_openpyxl()

    # ---- read Excel ----
    nodes_in = _read_nodes_sheet(input_xlsx)
    elems_in = _read_elements_sheet(input_xlsx)   # may be empty; that's fine

    # Debug: print bounding box
    _log(
        "Bounds:",
        f"X [{nodes_in['X'].min()}, {nodes_in['X'].max()}], "
        f"Y [{nodes_in['Y'].min()}, {nodes_in['Y'].max()}], "
        f"Z [{nodes_in['Z'].min()}, {nodes_in['Z'].max()}]"
    )

    # ---- validate required sheets before doing anything else ----
    if nodes_in.empty:
        raise ValueError("Nodes sheet is empty.")

    # Try to read Areas (quietly skip if the sheet isn't present)
    try:
        areas_in = _read_areas_sheet(input_xlsx)
        if areas_in is not None and areas_in.empty:
            areas_in = None
    except Exception:
        areas_in = None

    # Ne is still useful for orientation logic, but not critical
    Ne_inferred = _infer_ne_from_filename(input_xlsx) or 4

    sap, model = _start_sap2000_v20(visible=visible)
    try:
        # --- Define/ensure material before creating any sections ---
        selected_material = _ensure_material_defined(model, material) if material else None
        default_mat = selected_material or "CONC40"

        # ⬇️ ALWAYS build points; frames are created only if there are rows in elems_in
        _build_model_from_excel(
            model,
            nodes_in,
            elems_in,
            default_section=("RECT_300x500", default_mat, (0.30, 0.50))
        )

        # Build areas (shells) with default shell property that uses the chosen material
        if areas_in is not None:
            _build_areas_from_excel(
                model,
                areas_in,
                default_section=("SHELL_200", default_mat, 0.20)
            )

            # --- Orient area local axes based on node labels (1 and E+3) ---
            try:
                nodes_tot = len(nodes_in)
                E = int(round(nodes_tot ** 0.5)) - 1
                behind_name = str(E + 3)
                _set_area_axes_by_two_joints(
                    model,
                    joint1="1",
                    joint2=behind_name,
                    plane="31",
                    replace=True
                )
            except Exception:
                # Fallback to historical 1 & 23 convention
                _set_area_axes_by_two_joints(
                    model,
                    joint1="1",
                    joint2="23",
                    plane="31",
                    replace=True
                )

        # IMPORTANT: we **do not** replicate anything inside SAP2000 any more.
        # The full umbrella geometry (all tympans) is already present in the Nodes/Areas.

        # Extract back full model (for consistent results workbook)
        nodes, elems, areas = _extract_model_to_dfs(model)

        # Fix supports using full umbrella nodes
        _fix_base_nodes(model, nodes)
        _add_default_self_weight(model, "Dead", 1.0)

        # Save model beside spreadsheet (e.g., umbrella.sdb)
        base, _ = os.path.splitext(input_xlsx)
        sdb_path = base + ".sdb"
        try:
            model.File.Save(sdb_path)
        except Exception:
            pass

        _run_analysis(model)

        # Ensure only the desired case is selected for output
        try:
            model.Results.Setup.DeselectAllCasesAndCombosForOutput()
            model.Results.Setup.SetCaseSelectedForOutput("Dead")
        except Exception:
            pass

        # --- Soil sweep (only if provided) ---
        soil_results = None
        if soil:
            try:
                replace_area_load_each_step = soil.get("replace_each_step", True)
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

        # Debug
        _log(f"[run] nodes_df={len(nodes)} rows, elems_df={len(elems)} rows")
        if areas is not None:
            _log(f"[run] areas_df={len(areas)} rows")

        if "Name" in nodes.columns:
            _log(f"[run] first 5 node names: {nodes['Name'].astype(str).tolist()[:5]}")

        if "Frame" in elems.columns:
            _log(f"[run] first 5 frame names: {elems['Frame'].astype(str).tolist()[:5]}")
        else:
            _log("[run] no 'Frame' column in elems (model may have only areas/shells).")

        # Results
        node_names = nodes["Name"].astype(str).tolist()
        frame_names = elems["Frame"].astype(str).tolist() if ("Frame" in elems.columns and len(elems) > 0) else []

        import traceback
        try:
            disp_df = _collect_joint_displacements(model, node_names, "Dead")
            if frame_names:
                force_df = _collect_frame_end_forces(model, frame_names, "Dead")
            else:
                force_df = pd.DataFrame()
        except Exception:
            _log("[Error] While collecting results:")
            _log(traceback.format_exc())
            raise

        results_xlsx = base + "_results.xlsx"
        _ensure_xlsxwriter()

        with pd.ExcelWriter(results_xlsx, engine="xlsxwriter") as xlw:
            nodes.to_excel(xlw, sheet_name="Nodes", index=False)
            elems.to_excel(xlw, sheet_name="Elements", index=False)
            if areas is not None and not areas.empty:
                areas.to_excel(xlw, sheet_name="Areas", index=False)

            if not disp_df.empty:
                disp_df.to_excel(xlw, sheet_name="JointDisplacements", index=False)
            if not force_df.empty:
                force_df.to_excel(xlw, sheet_name="FrameEndForces", index=False)

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
