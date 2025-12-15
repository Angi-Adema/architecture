# umbrella.py (Optimized GUI Script)

import os
import sys
import socket
import subprocess
import numpy as np
import matplotlib.pyplot as plt
import xlsxwriter
from tkinter import *
from tkinter import messagebox
from PIL import ImageTk, Image

# Geometry generators
from src.dome import dome
from src.hypar import hypar
from src.pyramid import pyramid

# Global handle for last preview figure
LAST_FIG = None

# Set license env var BEFORE importing sap_integration
os.environ.setdefault("LM_LICENSE_FILE", "27000@pceasapp965.ucdenver.pvt")

# SAP2000 integration
from sap_integration import run_sap2000_analysis


# Reminder to login to VPN if not connected
def check_vpn_connection(host="pceasapp965.ucdenver.pvt", port=27000, timeout=3):
    """
    Try to connect to the SAP2000 license server to confirm VPN access.
    Returns True if reachable, False if not.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _normalize_point_name(v):
    """
    Convert things like 1, 1.0, '1.0' → '1' so we can reliably compare
    node names and area point references.
    """
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return str(v)


def validate_areas_against_nodes(areas, nodes):
    """
    Ensure P1..P4 (if given) are valid node names from the Nodes sheet.
    Works whether `areas` is a list of rows or a NumPy array and whether
    IDs are stored as 1, 1.0, "1", etc.
    """
    if areas is None:
        return

    # Normalize to a NumPy array for safe size/iteration
    areas_arr = np.asarray(areas, dtype=object)
    if areas_arr.size == 0:
        return

    def _canon(v):
        """Canonicalize a node/point ID so 1, 1.0, '1' all become '1'."""
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            f = float(s)
            if np.isnan(f):
                return None
            # treat any numeric ID as an integer string
            return str(int(round(f)))
        except Exception:
            # non-numeric: just return trimmed string
            return s

    # Nodes rows: [Name, X, Y, Z]
    node_names = {_canon(r[0]) for r in nodes}

    # Each area row: [AreaName, P1, P2, P3, P4?, Section?, Material?]
    for idx, row in enumerate(areas_arr, start=1):
        for p in row[1:5]:  # P1..P4
            cp = _canon(p)
            if cp is None:
                continue
            if cp not in node_names:
                raise ValueError(f"Areas row {idx}: point '{p}' not found in Nodes.")


# ---------------- Setup Paths (dev & PyInstaller) ---------------- #
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS                 # for bundled assets
    base_dir = os.path.dirname(sys.executable)
else:
    base_path = os.path.dirname(__file__)
    base_dir = base_path

output_dir = os.path.join(base_dir, "Output")
os.makedirs(output_dir, exist_ok=True)

# ---------------- GUI Setup ---------------- #
master_window = Tk()
master_window.title('Umbrella')

icon_path = os.path.join(base_path, 'logo.ico')
try:
    if os.path.exists(icon_path):
        master_window.iconbitmap(icon_path)
except Exception:
    # Ignore icon issues (e.g., on non-Windows or missing file)
    pass

root = Frame(master_window)
root.grid(row=0, column=0, sticky=W+E)

entry_width = 30
font_head = 'Helvetica 12 bold'
font_type = 'Helvetica 12'

Label(root, text='Enter geometric parameters', font=font_head).grid(
    row=0, column=0, columnspan=3, sticky=W
)

# Inputs
Label(root, text='Number of sides', font=font_type).grid(sticky=W, row=1, column=0)
ent_Ne = Entry(root, width=entry_width)
ent_Ne.grid(row=1, column=1)

Label(root, text='Length of Apothem (H)', font=font_type).grid(sticky=W, row=2, column=0)
ent_H = Entry(root, width=entry_width)
ent_H.grid(row=2, column=1)

Label(root, text='Rise of umbrella (Re)', font=font_type).grid(sticky=W, row=3, column=0)
ent_Re = Entry(root, width=entry_width)
ent_Re.grid(row=3, column=1)

Label(root, text='Number of elements along Apothem', font=font_type).grid(
    sticky=W, row=4, column=0
)
ent_N = Entry(root, width=entry_width)
ent_N.grid(row=4, column=1)

Label(
    root,
    text='Select tympan geometries to generate as SAP2000 input',
    font=font_head
).grid(row=5, column=0, columnspan=3, sticky=W)

# Geometry toggles
var_hypar = IntVar()
var_pyramid = IntVar()
var_dome = IntVar()

Checkbutton(
    root, text='Generate hypar tympan', font=font_type, variable=var_hypar
).grid(sticky=W, row=6, column=0, columnspan=2)
Checkbutton(
    root, text='Generate pyramidal tympan', font=font_type, variable=var_pyramid
).grid(sticky=W, row=7, column=0, columnspan=2)
Checkbutton(
    root, text='Generate dome tympan', font=font_type, variable=var_dome
).grid(sticky=W, row=8, column=0, columnspan=2)

# SAP2000 behavior toggles
var_autoclose = IntVar(value=0)   # 0 = keep SAP open after run; 1 = auto-close
Checkbutton(
    root,
    text='Auto-close SAP2000 after analysis',
    font=font_type,
    variable=var_autoclose
).grid(sticky=W, row=9, column=0, columnspan=2)

# --- Soil sweep inputs --- #
depth_min_var = DoubleVar(value=0.0)
depth_max_var = DoubleVar(value=4.0)
depth_step_var = DoubleVar(value=0.5)
gamma_var = DoubleVar(value=18000.0)   # N/m^3
axis_var = StringVar(value="Z")
normalize_var = BooleanVar(value=False)

# --- Material inputs --- #
mat_name_var = StringVar(value="CONC40")      # the material name you'll use in sections
mat_type_var = StringVar(value="Concrete")    # Concrete or Steel
mat_region_var = StringVar(value="User")      # "User" mimics your manual step
E_var = DoubleVar(value=30e9)                 # Elastic modulus [Pa]
nu_var = DoubleVar(value=0.20)                # Poisson's ratio
alpha_var = DoubleVar(value=1.0e-5)           # Thermal coeff [1/°C]
gamma_var_mat = DoubleVar(value=24000.0)      # Unit weight [N/m^3]

# --- Material section --- #
mat_frame = Frame(root)
mat_frame.grid(row=16, column=0, columnspan=3, sticky="we", pady=(6, 8))

Label(mat_frame, text='Material', font=font_head).grid(
    sticky=W, row=0, column=0, columnspan=4, pady=(0, 6)
)

Label(mat_frame, text='Name', font=font_type).grid(sticky=W, row=1, column=0)
Entry(mat_frame, textvariable=mat_name_var, width=12).grid(row=1, column=1)

Label(mat_frame, text='Type', font=font_type).grid(sticky=W, row=1, column=2)
OptionMenu(mat_frame, mat_type_var, "Concrete", "Steel").grid(
    row=1, column=3, sticky="we"
)

Label(mat_frame, text='Region', font=font_type).grid(sticky=W, row=2, column=0)
OptionMenu(mat_frame, mat_region_var, "User", "United States").grid(
    row=2, column=1, sticky="we"
)

Label(mat_frame, text='E [Pa]', font=font_type).grid(sticky=W, row=2, column=2)
Entry(mat_frame, textvariable=E_var, width=12).grid(row=2, column=3)

Label(mat_frame, text='ν', font=font_type).grid(sticky=W, row=3, column=0)
Entry(mat_frame, textvariable=nu_var, width=12).grid(row=3, column=1)

Label(mat_frame, text='α [1/°C]', font=font_type).grid(sticky=W, row=3, column=2)
Entry(mat_frame, textvariable=alpha_var, width=12).grid(row=3, column=3)

Label(mat_frame, text='γ [N/m³]', font=font_type).grid(sticky=W, row=4, column=0)
Entry(mat_frame, textvariable=gamma_var_mat, width=12).grid(row=4, column=1)

# ---------------- Load Schematic Image ---------------- #
img_path = os.path.join(base_path, 'Geometry.png')
if os.path.exists(img_path):
    try:
        img = Image.open(img_path)
        ratio = 0.7
        img_resized = img.resize((int(img.width * ratio), int(img.height * ratio)))
        schematic = ImageTk.PhotoImage(img_resized)
        img_label = Label(image=schematic)
        img_label.image = schematic  # prevent GC
        img_label.grid(row=19, column=0, columnspan=3, pady=(8, 0))
    except Exception:
        pass

# --- Soil sweep section (self-contained frame) --- #
soil_frame = Frame(root)
soil_frame.grid(row=10, column=0, columnspan=3, sticky="we", pady=(6, 8))

Label(
    soil_frame, text='Soil pressure sweep', font=font_head
).grid(sticky=W, row=0, column=0, columnspan=2, pady=(0, 6))

Label(soil_frame, text='Soil Depth Min (m)', font=font_type).grid(
    sticky=W, row=1, column=0
)
Entry(soil_frame, textvariable=depth_min_var, width=10).grid(row=1, column=1)

Label(soil_frame, text='Soil Depth Max (m)', font=font_type).grid(
    sticky=W, row=2, column=0
)
Entry(soil_frame, textvariable=depth_max_var, width=10).grid(row=2, column=1)

Label(soil_frame, text='Depth Step (m)', font=font_type).grid(
    sticky=W, row=3, column=0
)
Entry(soil_frame, textvariable=depth_step_var, width=10).grid(row=3, column=1)

Label(soil_frame, text='γ Soil (N/m³)', font=font_type).grid(
    sticky=W, row=4, column=0
)
Entry(soil_frame, textvariable=gamma_var, width=10).grid(row=4, column=1)

Label(soil_frame, text='Vertical Axis', font=font_type).grid(
    sticky=W, row=5, column=0
)
OptionMenu(soil_frame, axis_var, "X", "Y", "Z").grid(row=5, column=1, sticky="we")

Checkbutton(
    soil_frame,
    text='Normalize pattern (shape 0..1)',
    font=font_type,
    variable=normalize_var
).grid(sticky=W, row=6, column=0, columnspan=2, pady=(4, 0))


def _unpack_geometry(ret):
    """
    Normalize geometry return value to (nodes, elements, areas).

    - If the geometry function returns (nodes, elements, areas) already,
      we just pass them through.
    - If it returns (nodes, elements) like the original hypar.py, we treat
      'elements' as the quad connectivity and auto-build an Areas table
      for SAP2000.
    """
    import numpy as np

    if not isinstance(ret, tuple):
        raise ValueError("Geometry function must return a tuple.")

    # New-style: (nodes, elements, areas)
    if len(ret) == 3:
        nodes, elements, areas = ret
        return nodes, elements, areas

    # Original-style: (nodes, elements) where 'elements' is [id, P1, P2, P3, P4]
    if len(ret) == 2:
        nodes, elements = ret

        elems_arr = np.asarray(elements, dtype=float)
        areas_rows = []
        for row in elems_arr:
            # row: [AreaId, P1, P2, P3, P4]
            area_id = int(row[0])
            p1 = int(row[1])
            p2 = int(row[2])
            p3 = int(row[3])
            p4 = int(row[4])
            area_name = str(area_id)

            # Area row layout for our Excel writer:
            # [AreaName, P1, P2, P3, P4, Section, Material]
            areas_rows.append([
                area_name,
                str(p1), str(p2), str(p3), str(p4),
                "SHELL_200",   # default shell property name
                "CONC40",      # default material name
            ])

        areas = np.array(areas_rows, dtype=object)
        return nodes, elements, areas

    raise ValueError(f"Unexpected geometry return length: {len(ret)}")


def _has_plottable_nodes(nodes) -> bool:
    try:
        arr = np.asarray(nodes, dtype=object)
        xyz = np.asarray(arr[:, 1:4], dtype=float)  # X,Y,Z
        return np.isfinite(xyz).all(axis=1).any()
    except Exception:
        return False
    
def _replicate_radially(nodes, elements, areas, Ne, tol=1e-7):
    """
    Replicate a single tympan Ne times around global Z-axis AND WELD coincident nodes
    so adjacent tympans share joints (no SAP2000 seam/gap).

    nodes:    (n_nodes, 4) [ID, X, Y, Z]
    elements: (n_elems, 5) [ID, P1, P2, P3, P4]   (optional, used only if you need it)
    areas:    (n_areas, 7) [AreaName, P1, P2, P3, P4, Section, Material] or None

    Returns:
      nodes_full, elems_full, areas_full
    """
    import numpy as np

    if Ne <= 1:
        return (np.asarray(nodes, dtype=float),
                np.asarray(elements, dtype=float),
                None if areas is None else np.asarray(areas, dtype=object))

    base_nodes = np.asarray(nodes, dtype=float)
    base_elems = np.asarray(elements, dtype=float) if elements is not None else None
    base_areas = None if areas is None else np.asarray(areas, dtype=object)

    n_nodes_base = base_nodes.shape[0]
    n_elems_base = 0 if base_elems is None else base_elems.shape[0]
    angle_step = 2.0 * np.pi / float(Ne)

    # --- helper: coordinate hash for welding ---
    def _key(x, y, z):
        # quantize to a grid of size tol
        return (int(round(x / tol)), int(round(y / tol)), int(round(z / tol)))

    # Global welded node table
    key_to_gid = {}          # (qx,qy,qz) -> global node id (int)
    global_nodes = []        # [gid, x, y, z]
    next_gid = 1

    # For each copy, map local base node id -> welded global id
    # maps[k][local_id] = global_id
    maps = []

    for k in range(Ne):
        ang = k * angle_step
        c = np.cos(ang)
        s = np.sin(ang)

        local_to_global = {}
        for i in range(n_nodes_base):
            local_id = int(round(base_nodes[i, 0]))
            x, y, z = base_nodes[i, 1], base_nodes[i, 2], base_nodes[i, 3]

            xr = x * c - y * s
            yr = x * s + y * c
            zr = z

            kk = _key(xr, yr, zr)
            if kk in key_to_gid:
                gid = key_to_gid[kk]
            else:
                gid = next_gid
                next_gid += 1
                key_to_gid[kk] = gid
                global_nodes.append([gid, xr, yr, zr])

            local_to_global[local_id] = gid

        maps.append(local_to_global)

    nodes_full = np.array(global_nodes, dtype=float)

    # ----- replicate/weld elements (optional) -----
    all_elems = []
    if base_elems is not None and base_elems.size > 0:
        next_eid = 1
        for k in range(Ne):
            m = maps[k]
            for j in range(n_elems_base):
                # base_elems row: [eid, p1, p2, p3, p4]
                p1 = int(round(base_elems[j, 1]))
                p2 = int(round(base_elems[j, 2]))
                p3 = int(round(base_elems[j, 3]))
                p4 = int(round(base_elems[j, 4]))
                all_elems.append([next_eid, m[p1], m[p2], m[p3], m[p4]])
                next_eid += 1
    elems_full = np.array(all_elems, dtype=float) if all_elems else np.zeros((0, 5), dtype=float)

    # ----- replicate/weld areas (recommended for your shell model) -----
    all_areas = [] if base_areas is not None else None
    if base_areas is not None:
        for k in range(Ne):
            m = maps[k]
            for row in base_areas:
                area_name_base = str(row[0])
                p1 = int(round(float(row[1])))
                p2 = int(round(float(row[2])))
                p3 = int(round(float(row[3])))
                p4 = int(round(float(row[4])))
                sec = row[5]
                mat = row[6]

                # NOTE: node IDs are now welded global IDs
                new_area_name = f"{area_name_base}_{k+1}"
                all_areas.append([
                    new_area_name,
                    str(m[p1]),
                    str(m[p2]),
                    str(m[p3]),
                    str(m[p4]),
                    sec,
                    mat,
                ])

    areas_full = None if all_areas is None else np.array(all_areas, dtype=object)

    return nodes_full, elems_full, areas_full


# ---------------- Run Function ---------------- #
def run():
    # Validate inputs
    try:
        Ne = int(ent_Ne.get())
        H = float(ent_H.get())
        Re = float(ent_Re.get())
        N = int(ent_N.get())
    except ValueError:
        messagebox.showerror("Input Error", "Please enter valid numerical values.")
        return

    # Basic guards
    if Ne < 3:
        messagebox.showerror("Input Error", "Number of sides (Ne) must be ≥ 3.")
        return
    if H <= 0:
        messagebox.showerror("Input Error", "Apothem length H must be > 0.")
        return
    if N < 1:
        messagebox.showerror(
            "Input Error", "Elements along apothem (N) must be ≥ 1."
        )
        return
    if Re < 0:
        messagebox.showerror("Input Error", "Rise (Re) must be ≥ 0.")
        return

    if not (var_hypar.get() or var_pyramid.get() or var_dome.get()):
        messagebox.showwarning(
            "Selection", "Please select at least one geometry to generate."
        )
        return

    # --- Open Explorer only once per Run click --- #
    opened_dir = False

    print("[DEBUG] areas_full:", None if areas_full is None else len(areas_full), flush=True)

    # --- define the helper used below, AFTER inputs so it can use H/Ne/Re/N --- #
    def generate_and_export(name, nodes, elements, areas=None):
        nodes_arr = np.asarray(nodes, dtype=object)      # [Name/ID, X, Y, Z]
        elements_arr = np.asarray(elements, dtype=object)

        xlsx_name = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
        filepath = os.path.join(output_dir, xlsx_name)

        print(f"[Umbrella] Output directory: {output_dir}", flush=True)
        print(f"[Umbrella] Saving input workbook to: {filepath}", flush=True)

        # Validate Areas vs Nodes before writing file/SAP run
        try:
            if areas is not None and len(areas) > 0:
                validate_areas_against_nodes(areas, nodes_arr)
        except ValueError as e:
            messagebox.showerror("Areas validation", str(e))
            return
        has_areas = (areas is not None and len(areas) > 0)

        with xlsxwriter.Workbook(filepath, {'nan_inf_to_errors': True}) as wb:
            # ---------------- Nodes ----------------
            ws_nodes = wb.add_worksheet('Nodes')
            ws_nodes.write_row(0, 0, ["Name", "X", "Y", "Z"])  # OK (reader skips)

            for i, row in enumerate(nodes_arr, start=1):
                node_id = _normalize_point_name(row[0])   # <-- ADD THIS
                ws_nodes.write(i, 0, node_id)              # <-- USE IT HERE
                ws_nodes.write(i, 1, row[1])               # X
                ws_nodes.write(i, 2, row[2])               # Y
                ws_nodes.write(i, 3, row[3])               # Z
            # ---------------- Elements ----------------
            # IMPORTANT: if shell model uses Areas, Elements must be truly empty
            ws_elements = wb.add_worksheet('Elements')

            if not has_areas:
                # If you ever want frames, write them here (FrameName, I, J, Section, Material)
                for i, row in enumerate(elements_arr):
                    for j in range(5):
                        ws_elements.write(i, j, row[j] if j < len(row) else None)
            # else: do nothing; leave sheet blank

            # ---------------- Areas ----------------
            if has_areas:
                ws_areas = wb.add_worksheet('Areas')
                ws_areas.write_row(0, 0, ["Area", "P1", "P2", "P3", "P4", "Section", "Material"])

                for i, row in enumerate(areas, start=1):
                    for j, val in enumerate(row):
                        ws_areas.write(i, j, val)

        print(f"[Umbrella] Exists? {os.path.exists(filepath)}", flush=True)

        # Open folder (only once)
        nonlocal opened_dir
        if not opened_dir:
            try:
                subprocess.Popen(f'explorer "{output_dir}"')
            except Exception:
                pass
            opened_dir = True
        soil_spec = {
            "depth_min": depth_min_var.get(),
            "depth_max": depth_max_var.get(),
            "depth_step": depth_step_var.get(),
            "gamma": gamma_var.get(),
            "axis": axis_var.get(),
            "normalize": bool(normalize_var.get()),
            "load_pattern": "SOIL",
            "joint_pattern": "SOIL_DEPTH",
            "case_name": "SOIL_CASE",
        }
        material_spec = {
            "name":   mat_name_var.get(),
            "type":   mat_type_var.get(),
            "region": mat_region_var.get(),
            "E":      E_var.get(),
            "nu":     nu_var.get(),
            "alpha":  alpha_var.get(),
            "gamma":  gamma_var_mat.get(),
        }

        try:
            results = run_sap2000_analysis(
                filepath,
                visible=True,
                close_after=bool(var_autoclose.get()),
                soil=soil_spec,
                material=material_spec,
            )
            messagebox.showinfo(
                "SAP2000 Analysis Complete",
                f"Input:   {os.path.basename(filepath)}\n"
                f"Model:   {os.path.basename(results['model_path'])}\n"
                f"Results: {os.path.basename(results['results_path'])}\n\n"
                f"Nodes: {results['num_nodes']}   Frames: {results['num_frames']}"
                + (f"   Areas: {results.get('num_areas', 0)}" if 'num_areas' in results else "")
                + f"\nDisplacements rows: {results['disp_rows']}\n"
                + f"Frame forces rows: {results['force_rows']}\n"
                + f"Material: {material_spec['name']} ({material_spec['type']})"
            )
        except Exception as e:
            import traceback
            print("\n=== Umbrella caught exception ===\n", flush=True)
            traceback.print_exc()
            messagebox.showwarning("SAP2000 Error", f"Failed to run SAP2000 analysis:\n{e}")

    # -------- generate each selected geometry and analyze --------
    if var_hypar.get():
        ret = hypar(H, Re, Ne, N)
        nodes, elements, areas_data = _unpack_geometry(ret)

        # 🔁 build full umbrella from single tympan
        nodes_full, elements_full, areas_full = _replicate_radially(
            nodes, elements, areas_data, Ne
        )

        if not _has_plottable_nodes(nodes_full):
            messagebox.showerror(
                "Geometry error",
                "Hypar produced no valid XYZ coordinates. Check inputs."
            )
        else:
            generate_and_export("Hypar", nodes_full, elements_full, areas=areas_full)

    if var_pyramid.get():
        ret = pyramid(H, Re, Ne, N)
        nodes, elements, areas_data = _unpack_geometry(ret)

        nodes_full, elements_full, areas_full = _replicate_radially(
            nodes, elements, areas_data, Ne
        )

        if not _has_plottable_nodes(nodes_full):
            messagebox.showerror(
                "Geometry error",
                "Pyramid produced no valid XYZ coordinates. Check inputs."
            )
        else:
            generate_and_export("Pyramid", nodes_full, elements_full, areas=areas_full)

    if var_dome.get():
        ret = dome(H, Re, Ne, N)
        nodes, elements, areas_data = _unpack_geometry(ret)

        nodes_full, elements_full, areas_full = _replicate_radially(
            nodes, elements, areas_data, Ne
        )

        if not _has_plottable_nodes(nodes_full):
            messagebox.showerror(
                "Geometry error",
                "Dome produced no valid XYZ coordinates. Check inputs."
            )
        else:
            generate_and_export("Dome", nodes_full, elements_full, areas=areas_full)


def quit_app():
    """Cleanly close SAP2000 if it's running, then exit the GUI."""
    try:
        # Optional: ask the user first
        if not messagebox.askokcancel("Quit", "Close SAP2000 and exit Umbrella?"):
            return
    except Exception:
        # messagebox may not be initialized in some edge cases—proceed anyway
        pass

    closed = False

    # Close any Matplotlib preview windows
    try:
        import matplotlib.pyplot as plt
        plt.close('all')
    except Exception:
        pass

    # Clear the last-figure handle used by generate_and_export
    try:
        global LAST_FIG
        LAST_FIG = None
    except Exception:
        pass

    # Try the API paths that DO NOT launch a new instance
    try:
        import time
        import comtypes.client as cc

        # 1) Attach via ROT (running object table)
        try:
            sap = cc.GetActiveObject("CSI.SAP2000.API.SapObject")
            try:
                sap.ApplicationExit(True)  # True = no save prompt
                time.sleep(1.0)
                closed = True
            except Exception:
                pass
        except Exception:
            pass

        # 2) Attach via Helper.GetObject
        if not closed:
            try:
                helper = cc.CreateObject("SAP2000v1.Helper")
                sap = helper.GetObject("CSI.SAP2000.API.SapObject")
                sap.ApplicationExit(True)
                time.sleep(1.0)
                closed = True
            except Exception:
                pass

        # 3) Last resort: force close if API attach failed
        if not closed:
            try:
                # gentle try first (no /F)
                subprocess.run(
                    ["taskkill", "/IM", "SAP2000.exe", "/T"], capture_output=True
                )
                # ensure it’s gone
                subprocess.run(
                    ["taskkill", "/IM", "SAP2000.exe", "/T", "/F"], capture_output=True
                )
            except Exception:
                pass

    except Exception:
        # If comtypes or subprocess import fails, still proceed to close GUI
        pass

    # Finally, close the GUI
    try:
        master_window.destroy()
    except Exception:
        import os as _os
        _os._exit(0)


# Run button
Button(
    root, text='Run', width=18, height=2, command=run
).grid(row=6, column=2, rowspan=3, padx=(12, 0))

# Quit button
Button(
    root, text='Quit', width=18, height=2, command=quit_app
).grid(row=9, column=2, pady=(8, 0), padx=(12, 0))

# Quit Matplotlib preview window if "X" clicked
master_window.protocol("WM_DELETE_WINDOW", quit_app)

# ---------------- VPN / License Pre-Check ---------------- #
if not check_vpn_connection():
    messagebox.showerror(
        "VPN Required",
        "Could not reach the CU Denver SAP2000 license server.\n"
        "Please connect to the CU VPN before running Umbrella."
    )
    sys.exit(1)

# ---------------- Launch ---------------- #
root.mainloop()