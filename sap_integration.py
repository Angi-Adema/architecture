# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements)
# - Builds model in SAP2000
# - Runs analysis (Dead self-weight)
# - Exports results to <input>_results.xlsx

import importlib
import os
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
    """Coerce COM return 'x' to a sequence of length n.
    Handles singletons (scalar) and SAFEARRAYs that don't support len() cleanly.
    """
    if n is None:
        n = 0
    try:
        # Already a list/tuple-like with length?
        _ = len(x)  # may raise if x is scalar
        return list(x)
    except Exception:
        # Not iterable -> replicate scalar n times
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
                import time, os as _os
                _os.startfile(exe_path)
                _log("Launched EXE, attaching…")

                for _ in range(20):
                    time.sleep(1)
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
    # ---- Set units (guarded). Prefer enum; fallback to numeric 6 for v20 (kN–m–C). ----
    try:
        if hasattr(model, "SetPresentUnits"):
            if s2k and hasattr(s2k, "eUnits_N_m_C"):
                model.SetPresentUnits(s2k.eUnits_N_m_C)
            else:
                # NOTE: In SAP2000 v20, numeric code 6 = kN–m–C.
                # Fallback numeric code for v20 if enum isn't available:
                # (On many v20 builds: 8 = N–m–C; if wrong on your build, use the enum path above.)
                model.SetPresentUnits(8)
        else:
            _log("SetPresentUnits not available;leaving default units.")
    except Exception:
        _log("Unable to set present units; leaving default units.")

    # Optional: sanity check
    try:
        ret, units = model.GetPresentUnits()  # <- capital G
        _log(f"Present units code: {units}")
    except Exception:
        pass

    return sap, model


def _read_nodes_sheet(xlsx_path):
    """
    umbrella.py writes 'Nodes' without headers:
      col 0 = Name, col 3 = X, col 4 = Y, col 6 = Z
    Fallback to compact 4-col layout [Name, X, Y, Z] if needed.
    """
    df = pd.read_excel(xlsx_path, sheet_name="Nodes", header=None)
    name_col, x_col, y_col, z_col = 0, 3, 4, 6
    if df.shape[1] <= 4:
        name_col, x_col, y_col, z_col = 0, 1, 2, 3
    return pd.DataFrame({
        "Name": df.iloc[:, name_col].astype(str),
        "X":    df.iloc[:, x_col].astype(float),
        "Y":    df.iloc[:, y_col].astype(float),
        "Z":    df.iloc[:, z_col].astype(float),
    })


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
    name   = str(spec.get("name", "CONC40"))
    mtype  = str(spec.get("type", "Concrete")).strip().lower()
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

    # Convert inputs assuming model units = kN-m-C (as set in _start_sap2000_v20)
    E_pa   = float(spec.get("E", 30e9))         # Pa (= N/m^2)
    E_kPa  = E_pa / 1000.0                      # kN/m^2
    nu     = float(spec.get("nu", 0.2))
    alpha  = float(spec.get("alpha", 1.0e-5))   # 1/C

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
    mass_kN_s2_m4 = gamma_kNpm3 / 9.80665           # (kN/m^3) / g  => consistent mass density
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

    nodes_df columns (no headers in file; normalized before here): Name, X, Y, Z

    elems_df columns (no headers in file; normalized before here): Frame, I, J, Section (opt), Material (opt)

    default_section: (section_name, material_name, (b, h))
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
        i_pt  = str(r["I"]).strip()
        j_pt  = str(r["J"]).strip()

        raw_sec = str(r["Section"])  if pd.notna(r["Section"])  else sec_name
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
      - Triangles: P1,P2,P3 (P4 is None)
      - Quads:     P1,P2,P3,P4
    Also ensures a default shell section.
    """
    sec_name, mat_name, thick_m = default_section
    # Ensure a simple shell prop (thin/plate). Some versions use SetShell_1/2; try common names.
    try:
        model.PropArea.SetShell(sec_name, mat_name, float(thick_m))
    except Exception:
        try:
            model.PropArea.SetShell_1(sec_name, mat_name, float(thick_m))
        except Exception:
            pass
    try:
        model.PropMaterial.SetMaterial(mat_name, 2)  # 2 = concrete
    except Exception:
        pass

    for _, r in areas_df.iterrows():
        name = str(r["Area"])
        p1, p2, p3, p4 = str(r["P1"]), str(r["P2"]), str(r["P3"]), r["P4"]
        sec = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        mat = str(r["Material"]) if pd.notna(r["Material"]) else mat_name
        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, _mat_code_from_name(mat))
            except Exception:
                pass
        # (re)define the section if a custom one appears
        try:
            model.PropArea.SetShell(sec, mat, float(thick_m))
        except Exception:
            pass

        try:
            if p4 is None or (isinstance(p4, float) and pd.isna(p4)):
                # triangle (3 points)
                model.AreaObj.AddByPoint(p1, p2, p3, name)
            else:
                p4 = str(p4)
                model.AreaObj.AddByPoint(p1, p2, p3, p4, name)
            # assign section
            try:
                model.AreaObj.SetProperty(name, sec)
            except Exception:
                pass
        except Exception as e:
            raise RuntimeError(f"Failed to create area {name} ({p1},{p2},{p3},{p4}): {e}")


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
        except Exception as e:
            # skip bad node gracefully
            continue

        
        _log(f"[JointDispl] node={nname!r} -> nres={nres} | "
            f"types: Obj={type(Obj).__name__}, U1={type(U1).__name__}, "
            f"LoadCase={type(LoadCase).__name__}, StepType={type(StepType).__name__}, StepNum={type(StepNum).__name__}")


        n = int(nres or 0)
        if n == 0:
            continue

        # Coerce possible scalars into sequences of length n
        Obj      = _as_seq(Obj, n)
        LoadCase = _as_seq(LoadCase, n)
        StepType = _as_seq(StepType, n)
        StepNum  = _as_seq(StepNum, n)
        U1 = _as_seq(U1, n); U2 = _as_seq(U2, n); U3 = _as_seq(U3, n)
        R1 = _as_seq(R1, n); R2 = _as_seq(R2, n); R3 = _as_seq(R3, n)

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

        # Debug
        _log(f"[FrameForce] frame={fname!r} -> nres={nres} | "
            f"types: Obj={type(Obj).__name__}, P={type(P).__name__}, "
            f"LoadCase={type(LoadCase).__name__}, StepType={type(StepType).__name__}, StepNum={type(StepNum).__name__}")

        n = int(nres or 0)
        if n == 0:
            continue

        # Normalize to sequences
        Obj      = _as_seq(Obj, n)
        LoadCase = _as_seq(LoadCase, n)
        StepType = _as_seq(StepType, n)
        StepNum  = _as_seq(StepNum, n)
        P  = _as_seq(P,  n);  V2 = _as_seq(V2, n); V3 = _as_seq(V3, n)
        T  = _as_seq(T,  n);  M2 = _as_seq(M2, n); M3 = _as_seq(M3, n)

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
    API calls (names may vary slightly by version):
      - model.AreaObj.Count()
      - model.AreaObj.GetNameList()
    """
    try:
        ret, n_areas = model.AreaObj.Count()
        if int(n_areas) == 0:
            return []
        ret, names = model.AreaObj.GetNameList()
        try:
            return list(names)
        except Exception:
            return [names]
    except Exception:
        try:
            ret, names = model.AreaObj.GetNameList()
            try:
                return list(names)
            except Exception:
                return [names]
        except Exception:
            return []


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
    """
    ret, names = model.PointObj.GetNameList()
    try:
        names = list(names)
    except Exception:
        names = [names]
    for nm in names:
        try:
            ret, x, y, z = model.PointObj.GetCoordCartesian(nm, "Global")
        except Exception:
            ret, x, y, z = model.PointObj.GetCoordCartesian(nm)
        yield nm, float(x), float(y), float(z)


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

    If normalize_pattern:
        pattern_value = (min(raw_depth, d) / d)   # 0..1
        multiplier    = gamma * d                 # N/m^2 at step depth d
    else:
        pattern_value = min(raw_depth, d)         # meters
        multiplier    = gamma                     # N/m^3
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

        # 1) Set joint pattern values for this step depth d
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

        # 2) Assign pressure to all areas via the joint pattern
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

        # 3) Solve and collect
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
    Build & analyze a model from 'Nodes', 'Elements' (and optional 'Areas') sheets,
    then write results to: <input_basename>_results.xlsx

    Args:
        input_xlsx: path to spreadsheet
        visible: show SAP2000 UI
        close_after: close SAP2000 when done
        soil: dict|None soil sweep config
        material: dict|None material definition to ensure & use by default
    """
    if not os.path.exists(input_xlsx):
        raise FileNotFoundError(input_xlsx)
    
    _ensure_openpyxl()

    nodes = _read_nodes_sheet(input_xlsx)
    elems = _read_elements_sheet(input_xlsx)

    # ---- validate required sheets before doing anything else ----
    if nodes.empty:
        raise ValueError("Nodes sheet is empty.")
    if elems.empty:
        raise ValueError("Elements sheet is empty.")

    # Try to read Areas (quietly skip if the sheet isn't present)
    try:
        areas = _read_areas_sheet(input_xlsx)
        if areas is not None and areas.empty:
            areas = None
    except Exception:
        areas = None

    sap, model = _start_sap2000_v20(visible=visible)
    try:
        # --- NEW: define/ensure material before creating any sections ---
        selected_material = _ensure_material_defined(model, material) if material else None
        default_mat = selected_material or "CONC40"

        # Build frames with default section that uses the chosen material
        _build_model_from_excel(
            model, nodes, elems,
            default_section=("RECT_300x500", default_mat, (0.30, 0.50))
        )

        # Build areas (shells) with default shell property that uses the chosen material
        if areas is not None:
            _build_areas_from_excel(
                model, areas,
                default_section=("SHELL_200", default_mat, 0.20)
            )

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
                # Read user pref for replacing vs stacking loads each step
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
            except Exception as e:  # <-- fixed typo
                _log("[SoilSweep] Skipped:", e)

        # Debug
        _log(f"[run] nodes_df={len(nodes)} rows, elems_df={len(elems)} rows")
        if areas is not None:
            _log(f"[run] areas_df={len(areas)} rows")
        _log(f"[run] first 5 node names: {nodes['Name'].astype(str).tolist()[:5]}")
        _log(f"[run] first 5 frame names: {elems['Frame'].astype(str).tolist()[:5]}")

        # Results
        node_names = nodes["Name"].astype(str).tolist()
        frame_names = elems["Frame"].astype(str).tolist()

        import traceback
        try:
            disp_df = _collect_joint_displacements(model, node_names, "Dead")
            force_df = _collect_frame_end_forces(model, frame_names, "Dead")
        except Exception:
            _log("[Error] While collecting results:")
            _log(traceback.format_exc())
            raise

        results_xlsx = base + "_results.xlsx"

        _ensure_xlsxwriter()

        with pd.ExcelWriter(results_xlsx, engine="xlsxwriter") as xlw:
            # Always write model definition sheets
            nodes.to_excel(xlw, sheet_name="Nodes", index=False)
            elems.to_excel(xlw, sheet_name="Elements", index=False)

            if areas is not None:
                areas.to_excel(xlw, sheet_name="Areas", index=False)

            if not disp_df.empty:
                disp_df.to_excel(xlw, sheet_name="JointDisplacements", index=False)
            if not force_df.empty:
                force_df.to_excel(xlw, sheet_name="FrameEndForces", index=False)
            
            # Optional Soil config and summary
            if soil_results:
                # a) Echo the soil config used
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

                # b) One-row-per-step summary (depth + multiplier)
                #    Rebuild multiplier exactly the way the sweep used it:
                depths = soil_results["depths"]
                normalize = soil.get("normalize", False)
                gamma_val = soil.get("gamma", 18000.0)
                summary_rows = []
                for d in depths:
                    mult = (gamma_val * d) if normalize else gamma_val
                    summary_rows.append({"Depth_m": d, "Multiplier": mult})
                pd.DataFrame(summary_rows).to_excel(xlw, sheet_name="SoilSummary", index=False)
            if soil_results:
                for depth_val, ddf in zip(soil_results["depths"], soil_results["displacements_by_step"]):
                    if ddf.empty:
                        continue
                    dtag = f"d{str(depth_val).replace('.', '_')}"
                    ddf.to_excel(xlw, sheet_name=f"SoilDisp_{dtag}", index=False)

        return {
            "model_path": sdb_path,
            "results_path": results_xlsx,
            "num_nodes": len(nodes),
            "num_frames": len(elems),
            "num_areas": (0 if areas is None else len(areas)),
            "disp_rows": 0 if disp_df.empty else len(disp_df),
            "force_rows": 0 if force_df.empty else len(force_df),
        }
    finally:
        try:
            # only call if `sap` exists in this scope and we actually want to close/keep hidden
            if 'sap' in locals() and (close_after or not visible):
                sap.ApplicationExit(True)  # True => don't prompt to save
        except Exception:
            pass







