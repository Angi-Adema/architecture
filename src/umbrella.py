# umbrella.py (Optimized GUI Script)

import os
import numpy as np
import matplotlib.pyplot as plt
import xlsxwriter
from tkinter import *
from tkinter import messagebox
from PIL import ImageTk, Image

from .dome import dome
from .hypar import hypar
from .pyramid import pyramid

# ---------------- GUI Setup ---------------- #
master_window = Tk()
master_window.title('Umbrella')
# master_window.iconbitmap(os.path.join(os.getcwd(), 'logo.ico'))  # Uncomment if you have a logo.ico file

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

# Load and display schematic image once
img_path = os.path.join(os.getcwd(), 'Geometry.png')   # Combine current working directory with image to create the full file path
if os.path.exists(img_path):  # Check if the file path exists before trying to open it to prevent file not found errors
    img = Image.open(img_path)  # Uses PIL (Python Imaging Library) to open the image file
    ratio = 0.7
    img_resized = img.resize((int(img.width * ratio), int(img.height * ratio)))  # Scales the original image down to 70% its original size
    schematic = ImageTk.PhotoImage(img_resized)  # Converts the resized image to a format Tkinter can display (ImageTk.PhotoImage)
    Label(image=schematic).grid(row=10, column=0)  # Places the image inside a Label widget and shows in GUI grid at row 10, column 0

Label(root, text='Select tympan geometries to generate as SAP2000 input', font=font_head).grid(row=5, column=0, columnspan=2)

var_hypar = IntVar()
var_pyramid = IntVar()
var_dome = IntVar()

# If not planning to reuse or reference this, we would not assign this to a variable. If we do need to change the button later, use this:
#    c_hypar = Checkbutton(root, text='Generate hypar tympan', font=font_type, variable=var_hypar)
#    c_hypar.grid(sticky=W, row=6, column=0)
# This way, c_hypar holds a reference to the widget.
Checkbutton(root, text='Generate hypar tympan', font=font_type, variable=var_hypar).grid(sticky=W, row=6, column=0)
Checkbutton(root, text='Generate pyramidal tympan', font=font_type, variable=var_pyramid).grid(sticky=W, row=7, column=0)
Checkbutton(root, text='Generate parabolic tympan', font=font_type, variable=var_dome).grid(sticky=W, row=8, column=0)

# ---------------- Run Function ---------------- #
def run():
    try:
        Ne = int(ent_Ne.get())
        H = float(ent_H.get())
        Re = float(ent_Re.get())
        N = int(ent_N.get())
    except ValueError:
        messagebox.showerror("Input Error", "Please enter valid numerical values.")
        return

    # Reusable helper function. Reduces redundant code of 30+ lines for each geometry type. Easier to maintain and update.
    def generate_and_export(name, nodes, elements):
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.set_xlim([-H, H])
        ax.set_ylim([-H, H])
        ax.set_zlim([-H, H])
        ax.scatter(nodes[:, 1], nodes[:, 2], nodes[:, 3], color='black')
        plt.tight_layout()
        plt.show()

        filename = f"{name}{Ne}_H{H}_R{Re}_N{N}.xlsx"
        wb = xlsxwriter.Workbook(filename)

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

    if var_hypar.get():
        nodes, elements = hypar(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
        generate_and_export("Hypar", nodes, elements)

    if var_pyramid.get():
        nodes, elements = pyramid(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
        generate_and_export("Pyramid", nodes, elements)

    if var_dome.get():
        nodes, elements = dome(H, Re, Ne, N)   # Call the geometry function once and unpack both arrays efficiently.
        generate_and_export("Parabola", nodes, elements)
    
    # Now we can add more shape types easily:
    #   if var_ellipse.get():
    #       nodes, elements = ellipse(H, Re, Ne, N)
    #       generate_ and_export("Ellipse", nodes, elements)

# Run Button with optional threading for responsiveness
Button(root, text='Run', width=15, height=2, command=run).grid(row=6, column=1, rowspan=3)

# Start the GUI event loop
root.mainloop()