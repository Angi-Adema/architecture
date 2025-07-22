# Umbrella Geometry Generator

This Python-based desktop application provides a visual and data-generating tool for modeling geometric "tympan" surfaces—specifically **hypar**, **pyramid**, and **parabolic dome** shapes. It computes and exports nodal and element coordinates into Excel files for use in SAP2000 or other structural modeling software.

## 💻 Features

- GUI interface using `tkinter`
- Generates 3D geometry and visualizes it with `matplotlib`
- Saves output as `.xlsx` files in an organized `/Output` folder
- Supports 3 shape types:
  - Hypar (hyperbolic paraboloid)
  - Pyramid
  - Dome (paraboloid)

## 📁 Folder Structure

<pre>/umbrella-geometry-generator/
│
├── umbrella.py # Main GUI entry point
├── /src/
│ ├── dome.py # Dome geometry generation
│ ├── hypar.py # Hypar geometry generation
│ └── pyramid.py # Pyramid geometry generation
│
├── Output/ # Auto-created folder for Excel exports
│
├── logo.ico # Application icon
├── Geometry.png # Diagram shown in GUI
└── README.md</pre>

## 🛠 Requirements

- Python 3.10 or higher
- `numpy`
- `matplotlib`
- `xlsxwriter`
- `Pillow` (for displaying images in the GUI)

Install them with:

```bash
pip install numpy matplotlib xlsxwriter Pillow

## 🚀 How to Run

From the root directory:

    - python umbrella.py

## 🧾 How to Use

1. Launch the app
2. Enter the following geometric parameters:
    - Number of sides (Ne)
    - Apothem length (H)
    - Rise (Re)
    - Number of elements along apothem (N)
3. Check one or more geometry types to generate
4. Click Run
5. The Excel file(s) will be saved inside the /Output/ folder
6. A 3D preview will be displayed for each generated geometry

## 📦 Create a .exe File (Optional)

    - Run: pyinstaller --noconfirm --onefile --windowed --add-data "logo.ico;." --add-data "Geometry.png;." umbrella.py

    - The resulting .exe will still create an /Output/ folder in the same directory it runs from.

## 🧠 Notes

- The formulas for Z-coordinates vary by geometry type

- Excel sheets include:

    - Nodes tab: [Node#, X, Y, Z]

    - Elements tab: [Element ID, Node1, Node2, Node3, Node4 (if present)]

## 🧑‍💻 Developer Tips

- You can add new geometry types by:

    - Creating a new_shape.py in /src

    - Adding a checkbox and logic to umbrella.py following the generate_and_export() pattern

## Authors

Shengzhe (Jackson) Wang, Ph.D.

Angela E. Adema

Joshua P. Russell
