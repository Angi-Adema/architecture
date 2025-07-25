# You may need to adapt the column headers or sheet names if they’re different.
# For SAP2000 to find and add points correctly, point names must match those used in elements.
# AreaObj.AddByPoint() assumes quadrilateral areas.

import comtypes.client
import pandas as pd
import os

def read_excel_data(filepath):
    nodes = pd.read_excel(filepath, sheet_name='Nodes', usecols=[0, 1, 2, 3], names=['ID', 'X', 'Y', 'Z'], header=None)
    elements = pd.read_excel(filepath, sheet_name='Elements', usecols=[0, 1, 2, 3, 4], names=['ID', 'N1', 'N2', 'N3', 'N4'], header=None)
    return nodes, elements

def run_sap2000_analysis(filepath):
    # Load data
    nodes, elements = read_excel_data(filepath)

    # Start SAP2000
    comtypes.client.GetModule('C:\\Program Files\\Computers and Structures\\SAP2000 23\\SAP2000v1.dll')  # Update path
    import comtypes.gen.SAP2000v1 as sap
    helper = comtypes.client.CreateObject(sap.cHelper).QueryInterface(sap.cHelper)
    sap_obj = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
    sap_obj.ApplicationStart()
    model = sap_obj.SapModel

    # Initialize a new model
    model.InitializeNewModel()
    model.File.NewBlank()

    # Add nodes (joints)
    for _, row in nodes.iterrows():
        model.PointObj.AddCartesian(row['X'], row['Y'], row['Z'], f"N{int(row['ID'])}", "")

    # Add elements (frames or shells depending on structure type)
    for _, row in elements.iterrows():
        point_ids = [f"N{int(row[col])}" for col in ['N1', 'N2', 'N3', 'N4'] if pd.notnull(row[col])]
        if len(point_ids) == 2:
            model.FrameObj.AddByPoint(*point_ids, f"E{int(row['ID'])}")
        elif len(point_ids) == 3:
            model.AreaObj.AddByPoint(point_ids, f"E{int(row['ID'])}")
        elif len(point_ids) == 4:
            model.AreaObj.AddByPoint(point_ids, f"E{int(row['ID'])}")

    # Run the analysis
    model.Analyze.RunAnalysis()

    # Save the model
    save_path = os.path.splitext(filepath)[0] + "_Analyzed.sdb"
    model.File.Save(save_path)

    # Exit SAP2000
    sap_obj.ApplicationExit(False)
    print("SAP2000 analysis complete.")

