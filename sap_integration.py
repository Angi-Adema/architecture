# SAP2000 v20 integration used by umbrella.py
# - Reads umbrella.xlsx structure (Nodes, Elements)
# - Builds model in SAP2000 v20
# - Runs analysis (Dead self-weight)
# - Exports results to <input>_results.xlsx

import os
import pandas as pd
import comtypes.client

# ---- Paths for v20 (adjust if you installed elsewhere) ----
DIR_CANDIDATES = [
    r"C:\Program Files\SAP2000 20",
    r"C:\Program Files\Computers and Structures\SAP2000 20",
]

def _find_sap_paths():
    exe_path = None
    tlb_path = None
    for d in DIR_CANDIDATES:
        if not os.path.isdir(d):
            continue
        p_exe = os.path.join(d, "SAP2000.exe")
        p_tlb = os.path.join(d, "SAP2000v1.tlb")
        if os.path.isfile(p_exe) and exe_path is None:
            exe_path = p_exe
        if os.path.isfile(p_tlb) and tlb_path is None:
            tlb_path = p_tlb
    return exe_path, tlb_path

# Try to load v20 type library (nice to have: enums & signatures)
try:
    if os.path.exists(DEFAULT_TLB):
        comtypes.client.GetModule(DEFAULT_TLB)
        from comtypes.gen import SAP2000v1 as s2k
    else:
        s2k = None
except Exception:
    s2k = None


def _start_sap2000_v20(visible=True):
    """Start SAP2000 v20 using correct ProgIDs and return (sap_obj, model)."""
    helper = comtypes.client.CreateObject("SAP2000v1.Helper")
    if s2k:
        helper = helper.QueryInterface(s2k.cHelper)
    sap = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
    sap.ApplicationStart()  # launches v20

    try:
        sap.Visible(visible)
    except Exception:
        pass

    model = sap.SapModel
    model.InitializeNewModel()
    model.File.NewBlank()
    if s2k:
        model.SetPresentUnits(s2k.eUnits_kN_m_C)  # set your preferred units
    return sap, model


def _read_nodes_sheet(xlsx_path):
    """
    umbrella.py writes 'Nodes' without headers:
      col 0 = Name
      col 3 = X
      col 4 = Y
      col 6 = Z
    We'll map gracefully even if only 0..3 exist (old format).
    """
    df = pd.read_excel(xlsx_path, sheet_name="Nodes", header=None)
    # Default mapping
    name_col, x_col, y_col, z_col = 0, 3, 4, 6
    # Fallback for a compact 4-column layout
    if df.shape[1] <= 4:
        name_col, x_col, y_col, z_col = 0, 1, 2, 3

    out = pd.DataFrame({
        "Name": df.iloc[:, name_col].astype(str),
        "X":    df.iloc[:, x_col].astype(float),
        "Y":    df.iloc[:, y_col].astype(float),
        "Z":    df.iloc[:, z_col].astype(float),
    })
    return out


def _read_elements_sheet(xlsx_path):
    """
    umbrella.py writes 'Elements' without headers, first five columns used.
    We will interpret as:
      0: FrameName
      1: INode name (must exist in Nodes sheet)
      2: JNode name
      3: Section name (optional -> default if missing)
      4: Material (optional -> default if missing)
    """
    df = pd.read_excel(xlsx_path, sheet_name="Elements", header=None)
    # pad columns if fewer than 5 exist
    for c in range(df.shape[1], 5):
        df[c] = None
    return df.rename(columns={
        0: "Frame",
        1: "I",
        2: "J",
        3: "Section",
        4: "Material"
    })[["Frame", "I", "J", "Section", "Material"]]


def _ensure_default_section(model, section="RECT_300x500", material="CONC40",
                            dims=(0.30, 0.50), mat_type=2):
    """
    Creates a simple rectangular section if not present.
    mat_type: 1=Steel, 2=Concrete, ...
    """
    # Ensure material exists
    try:
        model.PropMaterial.SetMaterial(material, mat_type)
    except Exception:
        pass

    b, h = dims
    try:
        model.PropFrame.SetRectangle(section, material, b, h)
    except Exception:
        # If it already exists with different material, that's okay
        pass


def _build_model_from_excel(model, nodes_df, elems_df,
                            default_section=("RECT_300x500", "CONC40", (0.30, 0.50))):
    """Create points and frames using names from the spreadsheets."""
    sec_name, mat_name, dims = default_section

    # Make sure there is *some* section & material available
    _ensure_default_section(model, sec_name, mat_name, dims)

    # Add points
    for _, r in nodes_df.iterrows():
        name, x, y, z = r["Name"], r["X"], r["Y"], r["Z"]
        try:
            # returns (ret, new_name)
            model.PointObj.AddCartesian(x, y, z, str(name))
        except Exception:
            # likely duplicate names; ignore and continue
            pass

    # Add frames
    for _, r in elems_df.iterrows():
        fname = str(r["Frame"])
        i_pt  = str(r["I"])
        j_pt  = str(r["J"])
        sec   = str(r["Section"]) if pd.notna(r["Section"]) else sec_name
        mat   = str(r["Material"]) if pd.notna(r["Material"]) else mat_name

        # If a custom material provided, ensure it exists; then ensure section
        if pd.notna(r["Material"]):
            try:
                model.PropMaterial.SetMaterial(mat, 2)  # assume concrete
            except Exception:
                pass
        try:
            model.PropFrame.SetRectangle(sec, mat, 0.30, 0.50)
        except Exception:
            pass

        # Create by named points
        try:
            # returns (ret, new_frame_name)
            model.FrameObj.AddByPoint(i_pt, j_pt, sec, fname, "Global")
        except Exception as e:
            # If points missing or wrong names, this will fail.
            raise RuntimeError(f"Failed to create frame {fname} ({i_pt}->{j_pt}): {e}")

# Automatically restrain the min-Z so analyses don't go unstable. If we want pins instead of full fixity, change the tuple to (1,1,1,0,0,0).
def _fix_base_nodes(model, nodes_df, tol=1e-6, fix=(1,1,1,1,1,1)):
    """
    Fix nodes whose Z is at the minimum Z (within tol).
    fix = (UX, UY, UZ, RX, RY, RZ) as 0/1 flags.
    Example: fixed base (1,1,1,1,1,1), pinned base (1,1,1,0,0,0).
    Assumes global Z is vertical (SAP2000 default).
    """
    zmin = float(nodes_df["Z"].min())
    base = nodes_df.loc[(nodes_df["Z"] - zmin).abs() <= tol, "Name"].astype(str).tolist()
    for n in base:
        # PointObj.SetRestraint(Name, UX, UY, UZ, RX, RY, RZ)
        model.PointObj.SetRestraint(n, *fix)


def _add_default_self_weight(model, pattern="Dead", mult=1.0):
    """Create a Dead load pattern and set self-weight multiplier so you get non-zero results."""
    try:
        model.LoadPatterns.Add(pattern, 1, mult)  # 1 = Dead
    except Exception:
        pass


def _run_analysis(model):
    ret = model.Analyze.RunAnalysis()
    return ret


def _collect_joint_displacements(model, node_names, case="Dead"):
    rows = []
    for n in node_names:
        # v20 signature returns arrays
        ret, Obj, Elm, StepType, StepNum, U1, U2, U3, R1, R2, R3 = \
            model.Results.JointDispl(str(n), 0, case)
        for i in range(len(Obj)):
            rows.append({
                "Node": Obj[i], "Case": case,
                "UX": U1[i], "UY": U2[i], "UZ": U3[i],
                "RX": R1[i], "RY": R2[i], "RZ": R3[i],
            })
    return pd.DataFrame(rows)


def _collect_frame_end_forces(model, frame_names, case="Dead"):
    rows = []
    for f in frame_names:
        # StationType=1 => ends only
        ret, Obj, Elm, StepNum, P, V2, V3, T, M2, M3 = \
            model.Results.FrameForce(str(f), 1, case)
        for i in range(len(Obj)):
            end = "I" if (i % 2 == 0) else "J"
            rows.append({
                "Frame": Obj[i], "Case": case, "End": end,
                "P":  P[i], "V2": V2[i], "V3": V3[i],
                "T":  T[i], "M2": M2[i], "M3": M3[i],
            })
    return pd.DataFrame(rows)


def run_sap2000_analysis(input_xlsx, visible=True):
    """
    Entry point called by umbrella.py.
    Builds & analyzes a model from 'Nodes' and 'Elements' sheets, then writes results to:
      <input_basename>_results.xlsx
    Returns dict describing outputs.
    """
    if not os.path.exists(input_xlsx):
        raise FileNotFoundError(input_xlsx)

    # Read your umbrella-generated workbook
    nodes = _read_nodes_sheet(input_xlsx)
    elems = _read_elements_sheet(input_xlsx)

    # Start SAP2000 v20 and build
    sap, model = _start_sap2000_v20(visible=visible)
    try:
        _build_model_from_excel(model, nodes, elems)
        _fix_base_nodes(model, nodes)
        _add_default_self_weight(model, "Dead", 1.0)

        # Save a copy of the model next to the spreadsheet
        base, _ = os.path.splitext(input_xlsx)
        sdb_path = base + ".sdb"
        try:
            model.File.Save(sdb_path)
        except Exception:
            pass

        # Run analysis
        _run_analysis(model)

        # Collect results (Dead case by default)
        node_names  = nodes["Name"].astype(str).tolist()
        frame_names = elems["Frame"].astype(str).tolist()
        disp_df  = _collect_joint_displacements(model, node_names, "Dead")
        force_df = _collect_frame_end_forces(model, frame_names, "Dead")

        # Write results to a sibling file
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

