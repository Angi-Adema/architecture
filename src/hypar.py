# src/hypar.py
#
# Hypar generator that returns the *full umbrella*:
#  - nodes:   [Name, X, Y, Z]
#  - frames:  [FrameID, I, J, Section, Material]
#  - areas:   [AreaID, P1, P2, P3, P4, Section, Material]
#
# It builds one clean tympan (no zig-zags), then replicates it Ne times
# around the vertical Z-axis. All tympans share the same quad pattern,
# so the grid is consistent in every quadrant.

import math
import numpy as np


def hypar(H, Re, Ne, N):
    """
    Generate a full hypar umbrella.

    H  : apothem length
    Re : rise
    Ne : number of sides (e.g., 4)
    N  : number of elements along apothem
    """

    psi = np.pi / Ne  # half-wedge angle

    # ---------------- geometry helpers ---------------- #

    def get_z(xp, yp):
        delta_y = (H - xp) * np.sin(2 * psi) + xp * np.tan(psi)
        delta_x = (H - xp) * np.cos(2 * psi)
        z = Re * (1 - xp / H) * yp / np.sqrt(delta_x**2 + delta_y**2) + Re * xp / H
        return z

    def get_K(xp):
        delta_y = (H - xp) * np.sin(2 * psi) + xp * np.tan(psi)
        delta_x = (H - xp) * np.cos(2 * psi)
        psi_c = np.arctan(delta_x / delta_y)
        K = xp * np.sin(psi) / np.sin(np.pi / 2 - psi - psi_c)
        return K

    def get_xy(xp, yp):
        delta_y = (H - xp) * np.sin(2 * psi) + xp * np.tan(psi)
        delta_x = (H - xp) * np.cos(2 * psi)
        y = yp / np.sqrt(1 + (delta_x / delta_y) ** 2)
        x = xp + y * (delta_x / delta_y)
        return x, y

    def sym(x, y):
        """Mirror a point about the wedge bisector inside a single tympan."""
        theta = psi - np.arctan2(y, x)
        xr = x * np.cos(2 * theta) - y * np.sin(2 * theta)
        yr = x * np.sin(2 * theta) + y * np.cos(2 * theta)
        return xr, yr

    def rotate_xy(x, y, angle):
        c = math.cos(angle)
        s = math.sin(angle)
        return c * x - s * y, s * x + c * y

    # ---------------- base param grid for ONE tympan ---------------- #

    xp_base_i = np.linspace(0.0, H, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot = (N + 1) ** 2
    xp_i = np.zeros(nodes_tot)
    index_i = np.zeros(nodes_tot)

    # same construction you had before (keeps exactly your node ordering)
    n = 0
    for col in range(N + 1):
        for i in range(col + 1):
            xp_base_i[i] = xp_base_i[int(xp_index_i[col])]
            xp_index_i[i] = int(col)
        for s in range(N + 1):
            xp_i[n] = xp_base_i[s]
            index_i[n] = xp_index_i[s]
            n += 1

    yp_i = np.zeros(nodes_tot)
    n = 0
    for col in range(N + 1):
        for i in range(N + 1):
            index = min(i, col)
            yp_i[n] = np.linspace(0.0, get_K(xp_i[n]), int(index_i[n] + 1))[int(index)]
            n += 1

    # compute physical xyz for ONE tympan
    nodes_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = get_xy(xp_i[i], yp_i[i])
        nodes_ij[0, i] = x
        nodes_ij[1, i] = y
        nodes_ij[2, i] = get_z(xp_i[i], yp_i[i])

    # symmetry inside the wedge
    nodes_mod = nodes_ij.copy()
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                idx = i + (N + 1) * col
                x, y = sym(nodes_ij[0, idx], nodes_ij[1, idx])
                nodes_mod[0, idx] = x
                nodes_mod[1, idx] = y

    # rotate one tympan into place (your original -psi rotation)
    nodes_rot = np.zeros_like(nodes_mod)
    for i in range(nodes_tot):
        x, y = rotate_xy(nodes_mod[0, i], nodes_mod[1, i], -psi)
        nodes_rot[0, i] = x
        nodes_rot[1, i] = y
        nodes_rot[2, i] = nodes_mod[2, i]

    # ---------------- quads for ONE tympan ---------------- #

    ele_num = N ** 2
    base_quads = np.zeros((ele_num, 4), dtype=int)

    n = 0
    for col in range(N):
        for row in range(N):
            # This indexing is the same pattern you used before
            base = col + 1 + N * col + row
            p1 = base
            p2 = base + N + 1
            p3 = base + N + 2
            p4 = base + 1
            base_quads[n] = [p1, p2, p3, p4]
            n += 1

    # ---------------- replicate around Z for FULL umbrella ---------------- #

    all_nodes = []
    all_areas = []

    for k in range(Ne):
        angle = 2.0 * psi * k  # = 2π/Ne * k

        # nodes for tympan k
        for i in range(nodes_tot):
            x0, y0, z0 = nodes_rot[:, i]
            xk, yk = rotate_xy(x0, y0, angle)
            global_id = k * nodes_tot + (i + 1)  # 1-based node ID
            all_nodes.append([global_id, xk, yk, z0])

        # areas (quads) for tympan k
        node_offset = k * nodes_tot
        for e in range(ele_num):
            area_id = k * ele_num + (e + 1)  # 1-based across entire umbrella
            p1, p2, p3, p4 = base_quads[e]
            all_areas.append(
                [area_id, p1 + node_offset, p2 + node_offset, p3 + node_offset, p4 + node_offset]
            )

    # ---------------- frames from quad edges (for the grid lines) ---------------- #

    # Collect unique edges across all quads
    edge_map = {}  # (i, j) with i < j -> frame_id
    frame_id = 1

    for area in all_areas:
        _, p1, p2, p3, p4 = area
        edges = [(p1, p2), (p2, p3), (p3, p4), (p4, p1)]
        for i, j in edges:
            if i > j:
                i, j = j, i
            key = (i, j)
            if key not in edge_map:
                edge_map[key] = frame_id
                frame_id += 1

    frames = []
    for (i, j), fid in edge_map.items():
        # Section / Material here match what run_sap2000_analysis expects
        frames.append([fid, i, j, "RECT_300x500", "CONC40"])

    # ---------------- package for umbrella.py ---------------- #

    nodes_arr = np.asarray(all_nodes, dtype=float)
    frames_arr = np.asarray(frames, dtype=object)
    areas_sheet = []

    for area_id, p1, p2, p3, p4 in all_areas:
        areas_sheet.append(
            [int(area_id), int(p1), int(p2), int(p3), int(p4), "SHELL_200", "CONC40"]
        )

    areas_arr = np.asarray(areas_sheet, dtype=object)

    # umbrella.py expects plain Python lists (it casts back to np.array itself)
    return nodes_arr.tolist(), frames_arr.tolist(), areas_arr.tolist()
