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

Label(root, text='Enter geometric parameters', font=font_head).grid(row=0, column=0, columnspan=3, sticky=W)

# Inputs
Label(root, text='Number of sides', font=font_type).grid(sticky=W, row=1, column=0)
ent_Ne = Entry(root, width=entry_width); ent_Ne.grid(row=1, column=1)

Label(root, text='Length of Apothem (H)', font=font_type).grid(sticky=W, row=2, column=0)
ent_H = Entry(root, width=entry_width); ent_H.grid(row=2, column=1)

Label(root, text='Rise of umbrella (Re)', font=font_type).grid(sticky=W, row=3, column=0)
ent_Re = Entry(root, width=entry_width); ent_Re.grid(row=3, column=1)

Label(root, text='Number of elements along Apothem', font=font_type).grid(sticky=W, row=4, column=0)
ent_N = Entry(root, width=entry_width); ent_N.grid(row=4, column=1)

Label(root, text='Select tympan geometries to generate as SAP2000 input', font=font_head).grid(row=5, column=0, columnspan=3, sticky=W)

# Geometry toggles
var_hypar   = IntVar()
var_pyramid = IntVar()
var_dome    = IntVar()

Checkbutton(root, text='Generate hypar tympan',     font=font_type, variable=var_hypar).grid(   sticky=W, row=6, column=0, columnspan=2)
Checkbutton(root, text='Generate pyramidal tympan', font=font_type, variable=var_pyramid).grid( sticky=W, row=7, column=0, columnspan=2)
Checkbutton(root, text='Generate parabolic tympan', font=font_type, variable=var_dome).grid(    sticky=W, row=8, column=0, columnspan=2)

# SAP2000 behavior toggles
var_autoclose = IntVar(value=0)   # 0 = keep SAP open after run; 1 = auto-close
Checkbutton(root, text='Auto-close SAP2000 after analysis', font=font_type, variable=var_autoclose).grid(sticky=W, row=9, column=0, columnspan=2)

# --- Soil sweep inputs (Section B 1) ) ---
depth_min_var  = DoubleVar(value=0.0)
depth_max_var  = DoubleVar(value=4.0)
depth_step_var = DoubleVar(value=0.5)
gamma_var      = DoubleVar(value=18000.0)   # N/m^3
axis_var       = StringVar(value="Z")
normalize_var  = BooleanVar(value=False)

# --- Material inputs ---
mat_name_var = StringVar(value="CONC40")      # the material name you'll use in sections
mat_type_var = StringVar(value="Concrete")    # Concrete or Steel (add more if needed)
mat_region_var = StringVar(value="User")      # "User" mimics your manual step
E_var   = DoubleVar(value=30e9)               # Elastic modulus [Pa]
nu_var  = DoubleVar(value=0.20)               # Poisson's ratio
alpha_var = DoubleVar(value=1.0e-5)           # Thermal coeff [1/°C]
gamma_var_mat = DoubleVar(value=24000.0)      # Unit weight [N/m^3] (Concrete ~24 kN/m³)

# --- Material section ---
mat_frame = Frame(root)
mat_frame.grid(row=16, column=0, columnspan=3, sticky="we", pady=(6, 8))

Label(mat_frame, text='Material', font=font_head).grid(sticky=W, row=0, column=0, columnspan=4, pady=(0,6))

Label(mat_frame, text='Name', font=font_type).grid(sticky=W, row=1, column=0)
Entry(mat_frame, textvariable=mat_name_var, width=12).grid(row=1, column=1)

Label(mat_frame, text='Type', font=font_type).grid(sticky=W, row=1, column=2)
OptionMenu(mat_frame, mat_type_var, "Concrete", "Steel").grid(row=1, column=3, sticky="we")

Label(mat_frame, text='Region', font=font_type).grid(sticky=W, row=2, column=0)
OptionMenu(mat_frame, mat_region_var, "User", "United States").grid(row=2, column=1, sticky="we")

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

# --- Soil sweep section (self-contained frame) ---
soil_frame = Frame(root)
soil_frame.grid(row=10, column=0, columnspan=3, sticky="we", pady=(6, 8))

Label(soil_frame, text='Soil pressure sweep', font=font_head)\
    .grid(sticky=W, row=0, column=0, columnspan=2, pady=(0, 6))

Label(soil_frame, text='Soil Depth Min (m)', font=font_type)\
    .grid(sticky=W, row=1, column=0)
Entry(soil_frame, textvariable=depth_min_var, width=10)\
    .grid(row=1, column=1)

Label(soil_frame, text='Soil Depth Max (m)', font=font_type)\
    .grid(sticky=W, row=2, column=0)
Entry(soil_frame, textvariable=depth_max_var, width=10)\
    .grid(row=2, column=1)

Label(soil_frame, text='Depth Step (m)', font=font_type)\
    .grid(sticky=W, row=3, column=0)
Entry(soil_frame, textvariable=depth_step_var, width=10)\
    .grid(row=3, column=1)

Label(soil_frame, text='γ Soil (N/m³)', font=font_type)\
    .grid(sticky=W, row=4, column=0)
Entry(soil_frame, textvariable=gamma_var, width=10)\
    .grid(row=4, column=1)

Label(soil_frame, text='Vertical Axis', font=font_type)\
    .grid(sticky=W, row=5, column=0)
OptionMenu(soil_frame, axis_var, "X", "Y", "Z")\
    .grid(row=5, column=1, sticky="we")

Checkbutton(soil_frame, text='Normalize pattern (shape 0..1)', font=font_type,
            variable=normalize_var)\
    .grid(sticky=W, row=6, column=0, columnspan=2, pady=(4, 0))

# ---------------- Run Function ---------------- #
def run():
    # Validate inputs
    try:
        Ne = int(ent_Ne.get())
        H  = float(ent_H.get())
        Re = float(ent_Re.get())
        N  = int(ent_N.get())
    except ValueError:
        messagebox.showerror("Input Error", "Please enter valid numerical values.")
        return

    if not (var_hypar.get() or var_pyramid.get() or var_dome.get()):
        messagebox.showwarning("Selection", "Please select at least one geometry to generate.")
        return

    def generate_and_export(name, nodes, elements, areas=None):
        # Build filename & path in Output/
        xlsx_name = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
        filepath = os.path.join(output_dir, xlsx_name)

        # Preview nodes
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.set_xlim([-H, H]); ax.set_ylim([-H, H]); ax.set_zlim([-H, H])
        ax.scatter(nodes[:, 1], nodes[:, 2], nodes[:, 3], color='black')
        plt.tight_layout(); plt.show()

        # Write Excel workbook (always closes/saves, even if an error occurs mid-write)
        print(f"[Umbrella] Output directory: {output_dir}", flush=True)
        print(f"[Umbrella] Saving input workbook to: {filepath}", flush=True)

        # If areas provided, you may validate them against node names (optional) before writing
        # if areas:
        #     validate_areas_against_nodes(areas, nodes)

        if areas:
            validate_areas_against_nodes(areas, nodes)


        with xlsxwriter.Workbook(filepath) as wb:
            ws_nodes = wb.add_worksheet('Nodes')
            for i, row in enumerate(nodes):
                ws_nodes.write(i, 0, row[0])  # Name
                ws_nodes.write(i, 3, row[1])  # X
                ws_nodes.write(i, 4, row[2])  # Y
                ws_nodes.write(i, 6, row[3])  # Z
            
            # Elements (frames/lines)
            ws_elements = wb.add_worksheet('Elements')
            for i, row in enumerate(elements):
                # Expecting 5 cols: Frame, I, J, Section?, Material?
                for j in range(5):
                    val = row[j] if j < len(row) else None
                    ws_elements.write(i, j, val)
            
            # Areas (shells/surfaces)
            if areas:
                ws_areas = wb.add_worksheet('Areas')
                for i, row in enumerate(areas):
                    for j, val in enumerate(row):
                        ws_areas.write(i, j, val)

        # After the with-block (after the file is saved)
        print(f"[Umbrella] Exists? {os.path.exists(filepath)}", flush=True)

        # Open the folder automatically on Windows (optional)
        try:
            # if you moved `import subprocess` to the top, this is fine:
            subprocess.Popen(f'explorer "{output_dir}"')
            # Alternative that needs no import:
            # os.startfile(output_dir)
        except Exception:
            pass

        # Build specs from GUI
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
            "type":   mat_type_var.get(),      # "Concrete" or "Steel"
            "region": mat_region_var.get(),    # "User", "United States", etc.
            "E":      E_var.get(),
            "nu":     nu_var.get(),
            "alpha":  alpha_var.get(),
            "gamma":  gamma_var_mat.get(),     # N/m^3
            }
        # ---- Run SAP2000 analysis on this file ----
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

    # Generate each selected geometry and analyze
    if var_hypar.get():
        nodes, elements, areas_data = hypar(H, Re, Ne, N)
        generate_and_export("Hypar", nodes, elements, areas=areas_data)

    if var_pyramid.get():
        nodes, elements, areas_data = pyramid(H, Re, Ne, N)
        generate_and_export("Pyramid", nodes, elements, areas=areas_data)

    if var_dome.get():
        nodes, elements, areas_data = dome(H, Re, Ne, N)
        generate_and_export("Parabola", nodes, elements, areas=areas_data)


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

    # Try the API paths that DO NOT launch a new instance
    try:
        import time, subprocess
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

        # 2) Attach via Helper.GetObject (sometimes works when ROT doesn’t)
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
                subprocess.run(["taskkill", "/IM", "SAP2000.exe", "/T"], capture_output=True)
                # ensure it’s gone
                subprocess.run(["taskkill", "/IM", "SAP2000.exe", "/T", "/F"], capture_output=True)
            except Exception:
                pass

    except Exception:
        # If comtypes or subprocess import fails, still proceed to close GUI
        pass

    # Finally, close the GUI
    try:
        master_window.destroy()
    except Exception:
        # emergency exit if Tk is unhappy
        import os
        os._exit(0)

# Run button
Button(root, text='Run', width=18, height=2, command=run).grid(row=6, column=2, rowspan=3, padx=(12, 0))

# Quit button
Button(root, text='Quit', width=18, height=2, command=quit_app)\
    .grid(row=9, column=2, pady=(8, 0), padx=(12, 0))

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


# # umbrella.py (Optimized GUI Script)

# import os
# import sys
# import numpy as np
# import matplotlib.pyplot as plt
# import xlsxwriter
# from tkinter import *
# from tkinter import messagebox
# from PIL import ImageTk, Image

# # The goal is to keep the project modular and scalable, that being said I have restructured the files so the main file 'umbrella.py' is outside of the 'src' folder which holds all the geometry functions. Here I have updated the file paths accordingly.
# from src.dome import dome
# from src.hypar import hypar
# from src.pyramid import pyramid

# # Import SAP2000 integration module
# from sap_integration import run_sap2000_analysis

# # This block of code supports both the development environment as well as the PyInstaller .exe bundled packaging. This also prevents errors when __file__ doesn't work inside a compiled binary.
# # ---------------- Setup Paths ---------------- #
# if getattr(sys, 'frozen', False):
#     base_path = sys._MEIPASS  # For bundled assets like images
#     base_dir = os.path.dirname(sys.executable)  # Where the .exe is located
# else:
#     base_path = os.path.dirname(__file__)  # For dev mode
#     base_dir = base_path  # Where .py file is

# output_dir = os.path.join(base_dir, "Output")
# os.makedirs(output_dir, exist_ok=True)

# # ---------------- GUI Setup ---------------- #
# master_window = Tk()
# master_window.title('Umbrella')
# icon_path = os.path.join(base_path, 'logo.ico')
# master_window.iconbitmap(icon_path)
# #master_window.iconbitmap(os.path.join(os.getcwd(), 'logo.ico'))  See not above on Setup Paths.

# root = Frame(master_window)
# root.grid(row=0, column=0, sticky=W+E)

# entry_width = 30
# font_head = 'Helvetica 12 bold'
# font_type = 'Helvetica 12'

# Label(root, text='Enter geometric parameters', font=font_head).grid(row=0, column=0, columnspan=2)

# # Enter number of sides
# Label(root, text='Number of sides', font=font_type).grid(sticky=W, row=1, column=0)
# ent_Ne = Entry(root, width=entry_width)
# ent_Ne.grid(row=1, column=1)

# # Enter length of apothem
# Label(root, text='Length of Apothem (H)', font=font_type).grid(sticky=W, row=2, column=0)
# ent_H = Entry(root, width=entry_width)
# ent_H.grid(row=2, column=1)

# # Enter rise of umbrella
# Label(root, text='Rise of umbrella (Re)', font=font_type).grid(sticky=W, row=3, column=0)
# ent_Re = Entry(root, width=entry_width)
# ent_Re.grid(row=3, column=1)

# # Enter number of elements along apothem
# Label(root, text='Number of elements along Apothem', font=font_type).grid(sticky=W, row=4, column=0)
# ent_N = Entry(root, width=entry_width)
# ent_N.grid(row=4, column=1)

# Label(root, text='Select tympan geometries to generate as SAP2000 input', font=font_head).grid(row=5, column=0, columnspan=2)

# var_hypar = IntVar()
# var_pyramid = IntVar()
# var_dome = IntVar()

# # If not planning to reuse or reference this, we would not assign this to a variable. If we do need to change the button later, use this:
# # c_hypar = Checkbutton(root, text='Generate hypar tympan', font=font_type, variable=var_hypar)
# # c_hypar.grid(sticky=W, row=6, column=0)
# # This way, c_hypar holds a reference to the widget.
# Checkbutton(root, text='Generate hypar tympan', font=font_type, variable=var_hypar).grid(sticky=W, row=6, column=0)
# Checkbutton(root, text='Generate pyramidal tympan', font=font_type, variable=var_pyramid).grid(sticky=W, row=7, column=0)
# Checkbutton(root, text='Generate parabolic tympan', font=font_type, variable=var_dome).grid(sticky=W, row=8, column=0)

# # Load and display schematic image only once, original code did this twice which took up a lot of memory and slowed the program substantially.
# # img_path = os.path.join(os.getcwd(), 'Geometry.png')   
# # # Combine current working directory with image to create the full file path
# # ---------------- Load Schematic Image ---------------- #
# img_path = os.path.join(base_path, 'Geometry.png')
# if os.path.exists(img_path):  # Check if the file path exists before trying to open it to prevent file not found errors. Prevents crashes if image is missing.
#     img = Image.open(img_path)  # Uses PIL (Python Imaging Library) to open the image file
#     ratio = 0.7
#     img_resized = img.resize((int(img.width * ratio), int(img.height * ratio)))  # Scales the image cleanly without reloading or redundant PhotoImage calls.
#     schematic = ImageTk.PhotoImage(img_resized)  # Converts the resized image to a format Tkinter can display (ImageTk.PhotoImage)
#     img_label = Label(image=schematic)
#     img_label.image = schematic  # Prevent garbage collection
#     img_label.grid(row=10, column=0, columnspan=2)

#     #Label(image=schematic).grid(row=10, column=0)  # Places the image inside a Label widget and shows in GUI grid at row 10, column 0

# # ---------------- Run Function ---------------- #
# def run():
#     # Before there was no error handling and the program would crash if a field was left blank or if the user entered an invalid value. Now we prevent program crashing and gives users a helpful pop-up with instructions on what went wrong instead of a terminal stacktrace.
#     try:
#         Ne = int(ent_Ne.get())
#         H = float(ent_H.get())
#         Re = float(ent_Re.get())
#         N = int(ent_N.get())
#     except ValueError:
#         messagebox.showerror("Input Error", "Please enter valid numerical values.")
#         return

#     # Reusable helper function. Reduces redundant code of 30+ lines for each geometry type. Easier to maintain and update. Creates workbook, writes nodes and elements and plots the 3D view. Easier to debug, test and add new shapes.
#     def generate_and_export(name, nodes, elements):
#         # Puts the created Excel files into the Output folder and creates this folder if it does not exist. Prevents file clutter and makes .exe packaging predictable.
#         output_dir = os.path.join(os.getcwd(), "Output")
#         os.makedirs(output_dir, exist_ok=True)  # Create Output folder if it doesn't exist

#         filename = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
#         filepath = os.path.join(output_dir, filename)
        
#         fig = plt.figure()
#         ax = fig.add_subplot(111, projection='3d')
#         ax.set_xlim([-H, H])
#         ax.set_ylim([-H, H])
#         ax.set_zlim([-H, H])
#         ax.scatter(nodes[:, 1], nodes[:, 2], nodes[:, 3], color='black')
#         plt.tight_layout()
#         plt.show()

#         wb = xlsxwriter.Workbook(filepath)

#         ws_nodes = wb.add_worksheet('Nodes')
#         for i, row in enumerate(nodes):
#             ws_nodes.write(i, 0, row[0])
#             ws_nodes.write(i, 3, row[1])
#             ws_nodes.write(i, 4, row[2])
#             ws_nodes.write(i, 6, row[3])

#         ws_elements = wb.add_worksheet('Elements')
#         for i, row in enumerate(elements):
#             for j in range(5):
#                 ws_elements.write(i, j, row[j])

#         wb.close()

#     # Trigger SAP2000 analysis after Excel is written and provide safeguard against errors or crash.
#     try:
#         results = run_sap2000_analysis(filepath, visible=True, close_after=False)  
#         # Change close_after to True if want to close automatically after analysis
#         messagebox.showinfo(
#             "SAP2000 Analysis Complete",
#             f"Results written:\n{results['results_path']}\n\n"
#             f"Nodes: {results['num_nodes']}, Frames: {results['num_frames']}\n"
#             f"Displacements: {results['disp_rows']} rows\n"
#             f"Forces: {results['force_rows']} rows"
#         )
#     except Exception as e:
#         messagebox.showwarning("SAP2000 Error", f"Failed to run SAP2000 analysis:\n{e}")

#     # def generate_and_export(name, nodes, elements):
#     #     fig = plt.figure()
#     #     ax = fig.add_subplot(111, projection='3d')
#     #     ax.set_xlim([-H, H])
#     #     ax.set_ylim([-H, H])
#     #     ax.set_zlim([-H, H])
#     #     ax.scatter(nodes[:, 1], nodes[:, 2], nodes[:, 3], color='black')
#     #     plt.tight_layout()
#     #     plt.show()

#     #     filename = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
#     #     wb = xlsxwriter.Workbook(filename)

#     #     ws_nodes = wb.add_worksheet('Nodes')
#     #     for i, row in enumerate(nodes):
#     #         ws_nodes.write(i, 0, row[0])
#     #         ws_nodes.write(i, 3, row[1])
#     #         ws_nodes.write(i, 4, row[2])
#     #         ws_nodes.write(i, 6, row[3])

#     #     ws_elements = wb.add_worksheet('Elements')
#     #     for i, row in enumerate(elements):
#     #         for j in range(5):
#     #             ws_elements.write(i, j, row[j])

#     #     wb.close()

#     if var_hypar.get():
#         nodes, elements = hypar(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
#         generate_and_export("Hypar", nodes, elements)

#     if var_pyramid.get():
#         nodes, elements = pyramid(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
#         generate_and_export("Pyramid", nodes, elements)

#     if var_dome.get():
#         nodes, elements = dome(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
#         generate_and_export("Parabola", nodes, elements)
    
#     # Now we can easily add more shape types:
#     #   if var_ellipse.get():
#     #       nodes, elements = ellipse(H, Re, Ne, N)
#     #       generate_ and_export("Ellipse", nodes, elements)

# # Run Button with optional threading for responsiveness
# Button(root, text='Run', width=15, height=2, command=run).grid(row=6, column=1, rowspan=3)

# # ---------------- Launch ---------------- #
# # Start the GUI event loop
# root.mainloop()