# umbrella.py (Optimized GUI Script)

import os
import sys
import numpy as np
import matplotlib.pyplot as plt
import xlsxwriter
from tkinter import *
from tkinter import messagebox
from PIL import ImageTk, Image

# The goal is to keep the project modular and scalable, that being said I have restructured the files so the main file 'umbrella.py' is outside of the 'src' folder which holds all the geometry functions. Here I have updated the file paths accordingly.
from src.dome import dome
from src.hypar import hypar
from src.pyramid import pyramid

# This block of code supports both the development environment as well as the PyInstaller .exe bundled packaging. This also prevents errors when __file__ doesn't work inside a compiled binary.
# ---------------- Setup Paths ---------------- #
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS  # For bundled assets like images
    base_dir = os.path.dirname(sys.executable)  # Where the .exe is located
else:
    base_path = os.path.dirname(__file__)  # For dev mode
    base_dir = base_path  # Where .py file is

output_dir = os.path.join(base_dir, "Output")
os.makedirs(output_dir, exist_ok=True)

# ---------------- GUI Setup ---------------- #
master_window = Tk()
master_window.title('Umbrella')
icon_path = os.path.join(base_path, 'logo.ico')
master_window.iconbitmap(icon_path)
#master_window.iconbitmap(os.path.join(os.getcwd(), 'logo.ico'))  See not above on Setup Paths.

root = Frame(master_window)
root.grid(row=0, column=0, sticky=W+E)

entry_width = 30
font_head = 'Helvetica 12 bold'
font_type = 'Helvetica 12'

Label(root, text='Enter geometric parameters', font=font_head).grid(row=0, column=0, columnspan=2)

# Enter number of sides
Label(root, text='Number of sides', font=font_type).grid(sticky=W, row=1, column=0)
ent_Ne = Entry(root, width=entry_width)
ent_Ne.grid(row=1, column=1)

# Enter length of apothem
Label(root, text='Length of Apothem (H)', font=font_type).grid(sticky=W, row=2, column=0)
ent_H = Entry(root, width=entry_width)
ent_H.grid(row=2, column=1)

# Enter rise of umbrella
Label(root, text='Rise of umbrella (Re)', font=font_type).grid(sticky=W, row=3, column=0)
ent_Re = Entry(root, width=entry_width)
ent_Re.grid(row=3, column=1)

# Enter number of elements along apothem
Label(root, text='Number of elements along Apothem', font=font_type).grid(sticky=W, row=4, column=0)
ent_N = Entry(root, width=entry_width)
ent_N.grid(row=4, column=1)

Label(root, text='Select tympan geometries to generate as SAP2000 input', font=font_head).grid(row=5, column=0, columnspan=2)

var_hypar = IntVar()
var_pyramid = IntVar()
var_dome = IntVar()

# If not planning to reuse or reference this, we would not assign this to a variable. If we do need to change the button later, use this:
# c_hypar = Checkbutton(root, text='Generate hypar tympan', font=font_type, variable=var_hypar)
# c_hypar.grid(sticky=W, row=6, column=0)
# This way, c_hypar holds a reference to the widget.
Checkbutton(root, text='Generate hypar tympan', font=font_type, variable=var_hypar).grid(sticky=W, row=6, column=0)
Checkbutton(root, text='Generate pyramidal tympan', font=font_type, variable=var_pyramid).grid(sticky=W, row=7, column=0)
Checkbutton(root, text='Generate parabolic tympan', font=font_type, variable=var_dome).grid(sticky=W, row=8, column=0)

# Load and display schematic image only once, original code did this twice which took up a lot of memory and slowed the program substantially.
# img_path = os.path.join(os.getcwd(), 'Geometry.png')   
# # Combine current working directory with image to create the full file path
# ---------------- Load Schematic Image ---------------- #
img_path = os.path.join(base_path, 'Geometry.png')
if os.path.exists(img_path):  # Check if the file path exists before trying to open it to prevent file not found errors. Prevents crashes if image is missing.
    img = Image.open(img_path)  # Uses PIL (Python Imaging Library) to open the image file
    ratio = 0.7
    img_resized = img.resize((int(img.width * ratio), int(img.height * ratio)))  # Scales the image cleanly without reloading or redundant PhotoImage calls.
    schematic = ImageTk.PhotoImage(img_resized)  # Converts the resized image to a format Tkinter can display (ImageTk.PhotoImage)
    img_label = Label(image=schematic)
    img_label.image = schematic  # Prevent garbage collection
    img_label.grid(row=10, column=0, columnspan=2)

    #Label(image=schematic).grid(row=10, column=0)  # Places the image inside a Label widget and shows in GUI grid at row 10, column 0

# ---------------- Run Function ---------------- #
def run():
    # Before there was no error handling and the program would crash if a field was left blank or if the user entered an invalid value. Now we prevent program crashing and gives users a helpful pop-up with instructions on what went wrong instead of a terminal stacktrace.
    try:
        Ne = int(ent_Ne.get())
        H = float(ent_H.get())
        Re = float(ent_Re.get())
        N = int(ent_N.get())
    except ValueError:
        messagebox.showerror("Input Error", "Please enter valid numerical values.")
        return

    # Reusable helper function. Reduces redundant code of 30+ lines for each geometry type. Easier to maintain and update. Creates workbook, writes nodes and elements and plots the 3D view. Easier to debug, test and add new shapes.
    def generate_and_export(name, nodes, elements):
        # Puts the created Excel files into the Output folder and creates this folder if it does not exist. Prevents file clutter and makes .exe packaging predictable.
        output_dir = os.path.join(os.getcwd(), "Output")
        os.makedirs(output_dir, exist_ok=True)  # Create Output folder if it doesn't exist

        filename = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
        filepath = os.path.join(output_dir, filename)
        
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.set_xlim([-H, H])
        ax.set_ylim([-H, H])
        ax.set_zlim([-H, H])
        ax.scatter(nodes[:, 1], nodes[:, 2], nodes[:, 3], color='black')
        plt.tight_layout()
        plt.show()

        wb = xlsxwriter.Workbook(filepath)

        ws_nodes = wb.add_worksheet('Nodes')
        for i, row in enumerate(nodes):
            ws_nodes.write(i, 0, row[0])
            ws_nodes.write(i, 3, row[1])
            ws_nodes.write(i, 4, row[2])
            ws_nodes.write(i, 6, row[3])

        ws_elements = wb.add_worksheet('Elements')
        for i, row in enumerate(elements):
            for j in range(5):
                ws_elements.write(i, j, row[j])

        wb.close()

    # def generate_and_export(name, nodes, elements):
    #     fig = plt.figure()
    #     ax = fig.add_subplot(111, projection='3d')
    #     ax.set_xlim([-H, H])
    #     ax.set_ylim([-H, H])
    #     ax.set_zlim([-H, H])
    #     ax.scatter(nodes[:, 1], nodes[:, 2], nodes[:, 3], color='black')
    #     plt.tight_layout()
    #     plt.show()

    #     filename = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
    #     wb = xlsxwriter.Workbook(filename)

    #     ws_nodes = wb.add_worksheet('Nodes')
    #     for i, row in enumerate(nodes):
    #         ws_nodes.write(i, 0, row[0])
    #         ws_nodes.write(i, 3, row[1])
    #         ws_nodes.write(i, 4, row[2])
    #         ws_nodes.write(i, 6, row[3])

    #     ws_elements = wb.add_worksheet('Elements')
    #     for i, row in enumerate(elements):
    #         for j in range(5):
    #             ws_elements.write(i, j, row[j])

    #     wb.close()

    if var_hypar.get():
        nodes, elements = hypar(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
        generate_and_export("Hypar", nodes, elements)

    if var_pyramid.get():
        nodes, elements = pyramid(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
        generate_and_export("Pyramid", nodes, elements)

    if var_dome.get():
        nodes, elements = dome(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
        generate_and_export("Parabola", nodes, elements)
    
    # Now we can easily add more shape types:
    #   if var_ellipse.get():
    #       nodes, elements = ellipse(H, Re, Ne, N)
    #       generate_ and_export("Ellipse", nodes, elements)

# Run Button with optional threading for responsiveness
Button(root, text='Run', width=15, height=2, command=run).grid(row=6, column=1, rowspan=3)

# ---------------- Launch ---------------- #
# Start the GUI event loop
root.mainloop()