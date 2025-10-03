# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements)
# - Builds model in SAP2000
# - Runs analysis (Dead self-weight)
# - Exports results to <input>_results.xlsx

import importlib
import os
import pandas as pd
import comtypes.client as cc

# ---- Paths for v20 (adjust if installed elsewhere) ----
DIR_CANDIDATES = [
    r"C:\Program Files\SAP2000 20",
    r"C:\Program Files\Computers and Structures\SAP2000 20",
]


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


def _fix_base_nodes(model, nodes_df, tol=1e-6, fix=(1, 1, 1, 1, 1, 1)):
    """
    Fix nodes at the minimum Z (within tol).
    fix=(UX, UY, UZ, RX, RY, RZ); e.g., fixed base (1,1,1,1,1,1) or pinned (1,1,1,0,0,0).
    """
    zmin = float(nodes_df["Z"].min())
    base = nodes_df.loc[(nodes_df["Z"] - zmin).abs() <= tol, "Name"].astype(str).tolist()
    for n in base:
        model.PointObj.SetRestraint(n, *fix)


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
    # Defensive: select the case
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
        model.Results.Setup.SetCaseSelectedForOutput(case)
    except Exception:
        pass

    for n in node_names:
        # v20 signature:
        # (ret, NumberResults, Obj, Elm, LoadCase, StepType, StepNum, U1, U2, U3, R1, R2, R3)
        ret, nres, Obj, Elm, LoadCase, StepType, StepNum, U1, U2, U3, R1, R2, R3 = \
            model.Results.JointDispl(str(n), 0, case)
        if not nres:
            continue
        for i in range(nres):
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
    # Defensive: select the case
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
        model.Results.Setup.SetCaseSelectedForOutput(case)
    except Exception:
        pass

    for f in frame_names:
        # v20 signature:
        # (ret, NumberResults, Obj, Elm, LoadCase, StepType, StepNum, P, V2, V3, T, M2, M3)
        ret, nres, Obj, Elm, LoadCase, StepType, StepNum, P, V2, V3, T, M2, M3 = \
            model.Results.FrameForce(str(f), 1, case)  # 1 = ends only
        if not nres:
            continue
        for i in range(nres):
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


def run_sap2000_analysis(input_xlsx, visible=True):
    """
    Build & analyze a model from 'Nodes' and 'Elements' sheets, then write results to:
      <input_basename>_results.xlsx
    Returns a dict describing outputs.
    """
    if not os.path.exists(input_xlsx):
        raise FileNotFoundError(input_xlsx)

    nodes = _read_nodes_sheet(input_xlsx)
    elems = _read_elements_sheet(input_xlsx)

    sap, model = _start_sap2000_v20(visible=visible)
    try:
        _build_model_from_excel(model, nodes, elems)
        _fix_base_nodes(model, nodes)
        _add_default_self_weight(model, "Dead", 1.0)

        # Save model beside spreadsheet
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


        # Results
        node_names = nodes["Name"].astype(str).tolist()
        frame_names = elems["Frame"].astype(str).tolist()
        disp_df = _collect_joint_displacements(model, node_names, "Dead")
        force_df = _collect_frame_end_forces(model, frame_names, "Dead")

        results_xlsx = base + "_results.xlsx"
        with pd.ExcelWriter(results_xlsx, engine="xlsxwriter") as xlw:
            nodes.to_excel(xlw, sheet_name="Nodes", index=False)
            elems.to_excel(xlw, sheet_name="Elements", index=False)
            if not disp_df.empty:
                disp_df.to_excel(xlw, sheet_name="JointDisplacements", index=False)
            if not force_df.empty:
                force_df.to_excel(xlw, sheet_name="FrameEndForces", index=False)

        return {
            "model_path": sdb_path,
            "results_path": results_xlsx,
            "num_nodes": len(nodes),
            "num_frames": len(elems),
            "disp_rows": 0 if disp_df.empty else len(disp_df),
            "force_rows": 0 if force_df.empty else len(force_df),
        }
    finally:
        # keep SAP open if visible=True so you can inspect
        if not visible:
            try:
                sap.ApplicationExit(True)  # True => no save prompt
            except Exception:
                pass




# # You may need to adapt the column headers or sheet names if they’re different.
# # For SAP2000 to find and add points correctly, point names must match those used in elements.
# # AreaObj.AddByPoint() assumes quadrilateral areas.

# import comtypes.client
# import pandas as pd
# import os

# def read_excel_data(filepath):
#     nodes = pd.read_excel(filepath, sheet_name='Nodes', usecols=[0, 1, 2, 3], names=['ID', 'X', 'Y', 'Z'], header=None)
#     elements = pd.read_excel(filepath, sheet_name='Elements', usecols=[0, 1, 2, 3, 4], names=['ID', 'N1', 'N2', 'N3', 'N4'], header=None)
#     return nodes, elements

# def run_sap2000_analysis(filepath):
#     # Load data
#     nodes, elements = read_excel_data(filepath)

#     # Start SAP2000
#     comtypes.client.GetModule('C:\\Program Files\\Computers and Structures\\SAP2000 23\\SAP2000v1.dll')  # Update path
#     import comtypes.gen.SAP2000v1 as sap
#     helper = comtypes.client.CreateObject(sap.cHelper).QueryInterface(sap.cHelper)
#     sap_obj = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
#     sap_obj.ApplicationStart()
#     model = sap_obj.SapModel

#     # Initialize a new model
#     model.InitializeNewModel()
#     model.File.NewBlank()

#     # Add nodes (joints)
#     for _, row in nodes.iterrows():
#         model.PointObj.AddCartesian(row['X'], row['Y'], row['Z'], f"N{int(row['ID'])}", "")

#     # Add elements (frames or shells depending on structure type)
#     for _, row in elements.iterrows():
#         point_ids = [f"N{int(row[col])}" for col in ['N1', 'N2', 'N3', 'N4'] if pd.notnull(row[col])]
#         if len(point_ids) == 2:
#             model.FrameObj.AddByPoint(*point_ids, f"E{int(row['ID'])}")
#         elif len(point_ids) == 3:
#             model.AreaObj.AddByPoint(point_ids, f"E{int(row['ID'])}")
#         elif len(point_ids) == 4:
#             model.AreaObj.AddByPoint(point_ids, f"E{int(row['ID'])}")

#     # Run the analysis
#     model.Analyze.RunAnalysis()

#     # Save the model
#     save_path = os.path.splitext(filepath)[0] + "_Analyzed.sdb"
#     model.File.Save(save_path)

#     # Exit SAP2000
#     sap_obj.ApplicationExit(False)
#     print("SAP2000 analysis complete.")

