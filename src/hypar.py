import math
import numpy as np


def hypar(H, Re, Ne, N):
    """
    Generate a full umbrella of hypar tympans.

    Returns:
        nodes_full : (num_nodes, 4) array
            [Name, X, Y, Z]
        frames_full : (num_frames, 5) array
            [Frame, I, J, Section, Material]   (Section/Material left as NaN for defaults)
        areas_full : (num_areas, 5) array
            [Area, P1, P2, P3, P4]  (no section/material here; umbrella/run_sap2000 sets defaults)
    """
    psi = math.pi / Ne  # half sector angle

    def get_z(xp, yp):
        delta_y = (H - xp) * math.sin(2 * psi) + xp * math.tan(psi)
        delta_x = (H - xp) * math.cos(2 * psi)
        return Re * (1 - xp / H) * yp / math.sqrt(delta_x ** 2 + delta_y ** 2) + Re * xp / H

    def get_K(xp):
        delta_y = (H - xp) * math.sin(2 * psi) + xp * math.tan(psi)
        delta_x = (H - xp) * math.cos(2 * psi)
        psi_c = math.atan(delta_x / delta_y)
        return xp * math.sin(psi) / math.sin(math.pi / 2 - psi - psi_c)

    def get_xy(xp, yp):
        delta_y = (H - xp) * math.sin(2 * psi) + xp * math.tan(psi)
        delta_x = (H - xp) * math.cos(2 * psi)
        y = yp / math.sqrt(1 + (delta_x / delta_y) ** 2)
        x = xp + y * (delta_x / delta_y)
        return x, y

    def sym(x, y):
        # Reflect about the sector bisector
        theta = psi - math.atan2(y, x)
        xr = x * math.cos(2 * theta) - y * math.sin(2 * theta)
        yr = x * math.sin(2 * theta) + y * math.cos(2 * theta)
        return xr, yr

    # ------------------------------------------------------------------
    # 1) Base tympan (single sector) – exactly your original logic
    # ------------------------------------------------------------------
    xp_base_i = np.linspace(0.0, H, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot = (N + 1) ** 2
    xp_i = np.zeros(nodes_tot)
    index_i = np.zeros(nodes_tot)

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

    xs, ys, zs = [], [], []
    for i in range(nodes_tot):
        x, y = get_xy(float(xp_i[i]), float(yp_i[i]))
        z = get_z(float(xp_i[i]), float(yp_i[i]))
        xs.append(x)
        ys.append(y)
        zs.append(z)
    nodes_ij = np.vstack([xs, ys, zs])

    # Symmetrize within the sector
    nodes_mod = nodes_ij.copy()
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                idx = int(i + (N + 1) * col)
                xr, yr = sym(nodes_ij[0, idx], nodes_ij[1, idx])
                nodes_mod[0, idx] = xr
                nodes_mod[1, idx] = yr

    # Rotate by -psi so sector is centered
    cosA, sinA = math.cos(-psi), math.sin(-psi)
    nodes_rot = np.zeros_like(nodes_mod)
    for i in range(nodes_tot):
        x, y, z = nodes_mod[:, i]
        nodes_rot[0, i] = x * cosA - y * sinA
        nodes_rot[1, i] = x * sinA + y * cosA
        nodes_rot[2, i] = z

    # Base node table (single tympan)
    base_nodes = np.zeros((nodes_tot, 4), dtype=float)
    for i in range(nodes_tot):
        base_nodes[i] = [i + 1, nodes_rot[0, i], nodes_rot[1, i], nodes_rot[2, i]]

    # Base quad connectivity as AREAS: [Area, P1, P2, P3, P4]
    base_areas = np.zeros((N ** 2, 5), dtype=int)
    eid = 1
    for col in range(N):
        for row in range(N):
            base = int(col + 1 + N * col + row)
            base_areas[eid - 1] = [eid, base, base + N + 1, base + N + 2, base + 1]
            eid += 1

    # ------------------------------------------------------------------
    # 2) Replicate the base tympan Ne times around Z (umbrella)
    # ------------------------------------------------------------------
    n_nodes = nodes_tot
    n_areas = base_areas.shape[0]

    all_nodes = []
    all_areas = []

    for sector in range(Ne):
        angle = 2.0 * psi * sector   # 0, 2ψ, 4ψ, ... (for Ne=4 -> 0, 90, 180, 270)
        cosR, sinR = math.cos(angle), math.sin(angle)

        # rotate nodes
        for i in range(n_nodes):
            node_id = sector * n_nodes + int(base_nodes[i, 0])
            x, y, z = base_nodes[i, 1:4]
            xr = x * cosR - y * sinR
            yr = x * sinR + y * cosR
            all_nodes.append([node_id, xr, yr, z])

        # copy areas with node index offset
        for j in range(n_areas):
            area_id, p1, p2, p3, p4 = base_areas[j]
            area_id = sector * n_areas + area_id
            offset = sector * n_nodes
            all_areas.append([
                area_id,
                int(p1) + offset,
                int(p2) + offset,
                int(p3) + offset,
                int(p4) + offset,
            ])

    nodes_full = np.asarray(all_nodes, dtype=float)
    areas_full = np.asarray(all_areas, dtype=int)

    # ------------------------------------------------------------------
    # 3) Build FRAME elements as unique edges of all quads
    # ------------------------------------------------------------------
    edges = set()
    for _, p1, p2, p3, p4 in areas_full:
        for a, b in ((p1, p2), (p2, p3), (p3, p4), (p4, p1)):
            if a == b:
                continue
            key = tuple(sorted((int(a), int(b))))
            edges.add(key)

    frames_full = np.zeros((len(edges), 5), dtype=float)
    for fid, (i, j) in enumerate(sorted(edges), start=1):
        frames_full[fid - 1] = [fid, i, j, math.nan, math.nan]

    return nodes_full, frames_full, areas_full
