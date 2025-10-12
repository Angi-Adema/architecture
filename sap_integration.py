# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements)
# - Builds model in SAP2000
# - Runs analysis (Dead self-weight)
# - Exports results to <input>_results.xlsx

import importlib
import os
import pandas as pd
import numpy as np
import comtypes.client as cc

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
            # e.g., "SAP2000v20.tlb" -> module "SAP2000v20"
            module_name = os.path.splitext(os.path.basename(tlb_path))[0]
            s2k = importlib.import_module(f"comtypes.gen.{module_name}")
    except Exception:
        s2k = None  # not critical

    sap = None
    # Preferred: direct SapObject ProgID
    try:
        sap = cc.CreateObject("CSI.SAP2000.API.SapObject")
    except Exception:
        # Attach or last-resort launch then attach
        try:
            sap = cc.GetActiveObject("CSI.SAP2000.API.SapObject")
        except Exception:
            if not exe_path:
                raise RuntimeError("Could not locate SAP2000.exe. Update DIR_CANDIDATES.")
            import time
            os.startfile(exe_path)
            for _ in range(20):
                time.sleep(1)
                try:
                    sap = cc.GetActiveObject("CSI.SAP2000.API.SapObject")
                    break
                except Exception:
                    pass
            if sap is None:
                raise RuntimeError("SAP2000 did not register in time after launch.")

    # Start (safe if already running)
    try:
        sap.ApplicationStart()
    except Exception:
        pass
    try:
        sap.SetAsActiveObject()
    except Exception:
        pass

    # SAP2000 v20: Visible() takes no arguments
    if visible:
        try:
            sap.Visible()
        except Exception:
            pass

    model = sap.SapModel
    model.InitializeNewModel()
    model.File.NewBlank()

    # Use enums if available
    try:
        if s2k:
            model.SetPresentUnits(s2k.eUnits_kN_m_C)
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


def _build_model_from_excel(model, nodes_df, elems_df,
                            default_section=("RECT_300x500", "CONC40", (0.30, 0.50))):
    """Create points and frames using spreadsheet names."""
    sec_name, mat_name, dims = default_section
    _ensure_default_section(model, sec_name, mat_name, dims)

    # Points
    for _, r in nodes_df.iterrows():
        name, x, y, z = r["Name"], r["X"], r["Y"], r["Z"]
        try:
            model.PointObj.AddCartesian(x, y, z, str(name))
        except Exception:
            pass  # likely duplicate

    # Frames
    for _, r in elems_df.iterrows():
        fname = str(r["Frame"])
        i_pt = str(r["I"])
        j_pt = str(r["J"])
        sec = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        mat = str(r["Material"]) if pd.notna(r["Material"]) else mat_name

        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, 2)
            except Exception:
                pass
        try:
            model.PropFrame.SetRectangle(sec, mat, 0.30, 0.50)
        except Exception:
            pass

        try:
            model.FrameObj.AddByPoint(i_pt, j_pt, sec, fname, "Global")
        except Exception as e:
            raise RuntimeError(f"Failed to create frame {fname} ({i_pt}->{j_pt}): {e}")
        
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
                model.PropMaterial.SetMaterial(mat, 2)
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
    return model.Analyze.RunAnalysis()


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
    """
    try:
        model.LoadPatterns.Add(load_pattern_name, 1, 0.0)  # 1=Dead
    except Exception:
        pass

    _ensure_joint_pattern_exists(model, joint_pattern_name)

    area_names = _get_all_area_names(model)
    if not area_names:
        raise RuntimeError("No area (shell) objects found. Soil pressure must be assigned to areas.")

    joints = list(_iter_all_point_coords(model))
    if not joints:
        raise RuntimeError("No joints found in model to set joint-pattern values.")

    axis_map = {"X": 0, "Y": 1, "Z": 2}
    if vertical_axis.upper() not in axis_map:
        raise ValueError("vertical_axis must be one of 'X','Y','Z'")
    idx = axis_map[vertical_axis.upper()]

    surface_elev = max((xyz[idx] for (_, *xyz) in ((n, x, y, z) for n, x, y, z in joints)))

    results = []
    d = depth_min
    try:
        model.LoadCases.StaticLinear.SetCase(results_case_name)
        model.LoadCases.StaticLinear.SetLoads(results_case_name, 1, [load_pattern_name], [1.0])
    except Exception:
        pass

    while d <= depth_max + 1e-9:
        for joint_name, x, y, z in joints:
            elev = (x, y, z)[idx]
            raw_depth = max(0.0, surface_elev - elev)
            depth_for_step = min(raw_depth, d)
            if normalize_pattern:
                pattern_value = 0.0 if d <= 1e-12 else (depth_for_step / d)
                multiplier = gamma_soil_N_per_m3 * d  # N/m^2
            else:
                pattern_value = depth_for_step                 # m
                multiplier = gamma_soil_N_per_m3               # N/m^3
            _set_joint_pattern_value_for_point(model, joint_name, joint_pattern_name, pattern_value)

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

        disp_df.insert(0, "Depth_m", round(d, 3))
        disp_df.insert(1, "Multiplier", multiplier)
        if not force_df.empty:
            force_df.insert(0, "Depth_m", round(d, 3))
            force_df.insert(1, "Multiplier", multiplier)

        results.append((round(d, 3), disp_df, force_df))
        d = round(d + depth_step, 10)

    if results_book_path:
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


def run_sap2000_analysis(input_xlsx, visible=True, close_after=False):
    """
    Build & analyze a model from 'Nodes', 'Elements' (and optional 'Areas') sheets,
    then write results to: <input_basename>_results.xlsx
    Returns a dict describing outputs.
    """
    if not os.path.exists(input_xlsx):
        raise FileNotFoundError(input_xlsx)

    nodes = _read_nodes_sheet(input_xlsx)
    elems = _read_elements_sheet(input_xlsx)

    # Try to read Areas (quietly skip if the sheet isn't present)
    try:
        areas = _read_areas_sheet(input_xlsx)  # may raise if sheet missing
        if areas is not None and areas.empty:
            areas = None
    except Exception:
        areas = None

    sap, model = _start_sap2000_v20(visible=visible)
    try:
        _build_model_from_excel(model, nodes, elems)

        # Build areas if provided (triangles/quads by point names)
        if areas is not None:
            _build_areas_from_excel(model, areas)

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

        # Debug
        _log(f"[run] nodes_df={len(nodes)} rows, elems_df={len(elems)} rows")
        # Include areas count in debug, if present
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
        except Exception as e:
            _log("[Error] While collecting results:")
            _log(traceback.format_exc())
            raise

        results_xlsx = base + "_results.xlsx"
        with pd.ExcelWriter(results_xlsx, engine="xlsxwriter") as xlw:
            nodes.to_excel(xlw, sheet_name="Nodes", index=False)
            elems.to_excel(xlw, sheet_name="Elements", index=False)

            # Include Areas sheet in the output workbook (for traceability)
            if areas is not None:
                areas.to_excel(xlw, sheet_name="Areas", index=False)

            if not disp_df.empty:
                disp_df.to_excel(xlw, sheet_name="JointDisplacements", index=False)
            if not force_df.empty:
                force_df.to_excel(xlw, sheet_name="FrameEndForces", index=False)

        return {
            "model_path": sdb_path,
            "results_path": results_xlsx,
            "num_nodes": len(nodes),
            "num_frames": len(elems),
            # Optional: include number of areas in the return dict
            "num_areas": (0 if areas is None else len(areas)),
            "disp_rows": 0 if disp_df.empty else len(disp_df),
            "force_rows": 0 if force_df.empty else len(force_df),
        }
    finally:  # <-- align with the try:
        try:
            if close_after or not visible:
                sap.ApplicationExit(True)  # True => don't prompt to save
        except Exception:
            pass






