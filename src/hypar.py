import numpy as np

def hypar(H, Re, Ne, N):
    """
    Generate a full hypar umbrella with Ne tympans.

    Returns:
        nodes_all   : (num_nodes, 4)  => [ID, X, Y, Z]
        elements_all: (0, 5)          => kept empty (we use shells only)
        areas_all   : (num_areas, 7)  => [AreaName, P1, P2, P3, P4, Section, Material]
    """

    psi = np.pi / Ne  # half central angle of one tympan

    # ---- original helper functions (unchanged) ----
    def get_z(xp, yp):
        delta_y = (H - xp) * np.sin(2 * psi) + xp * np.tan(psi)
        delta_x = (H - xp) * np.cos(2 * psi)
        z = Re * (1 - xp / H) * yp / np.sqrt(delta_x ** 2 + delta_y ** 2) + Re * xp / H
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
        theta = psi - np.arctan(y / x)
        xr = x * np.cos(2 * theta) - y * np.sin(2 * theta)
        yr = x * np.sin(2 * theta) + y * np.cos(2 * theta)
        return xr, yr

    def rotate_xy(x, y, angle):
        """Rotate (x, y) by 'angle' (radians) about the origin."""
        c = np.cos(angle)
        s = np.sin(angle)
        xr = x * c - y * s
        yr = x * s + y * c
        return xr, yr

    # ---------- 1. BASE TYMPAN GEOMETRY (exactly as before) ----------

    # apothem stations
    xp_base_i = np.linspace(0, H, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot = int((N + 1) ** 2)  # nodes per tympan
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
            idx = min(i, col)
            yp_i[n] = np.linspace(0, get_K(xp_i[n]), int(index_i[n] + 1))[int(idx)]
            n += 1

    # compute base x,y,z
    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),) for i in range(nodes_tot)]
    ).T  # shape (3, nodes_tot)

    # mirror region to fill tympan
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                idx = int(i + (N + 1) * col)
                xr, yr = sym(nodes_ij[0][idx], nodes_ij[1][idx])
                nodes_mod_ij[0][idx] = xr
                nodes_mod_ij[1][idx] = yr

    # rotate so tympan sits in correct orientation
    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = rotate_xy(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # base quad connectivity for ONE tympan
    ele_num = int(N ** 2)  # quads per tympan
    ele_ij = np.zeros((ele_num, 5), dtype=int)
    n = 0
    for col in range(N):
        for row in range(N):
            base = int(col + 1 + N * col + row)
            # [AreaID (local), P1, P2, P3, P4]
            ele_ij[n] = [n + 1, base, base + N + 1, base + N + 2, base + 1]
            n += 1

    # base node table: [ID, X, Y, Z] for one tympan (IDs 1..nodes_tot)
    nod_ij = np.zeros((nodes_tot, 4), dtype=float)
    for i in range(nodes_tot):
        nod_ij[i] = [i + 1, nodes_rot_ij[0][i], nodes_rot_ij[1][i], nodes_rot_ij[2][i]]

    # ---------- 2. RADIAL REPLICATION IN PYTHON (Ne tympans) ----------

    all_nodes = []
    all_areas = []

    for k in range(Ne):
        angle = 2.0 * psi * k  # rotate each copy by 2ψ·k
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)

        node_offset = k * nodes_tot
        elem_offset = k * ele_num

        # nodes for tympan k
        for i in range(nodes_tot):
            base_id, bx, by, bz = nod_ij[i]
            gx = bx * cos_a - by * sin_a
            gy = bx * sin_a + by * cos_a
            gid = node_offset + int(base_id)  # global ID
            all_nodes.append([gid, gx, gy, bz])

        # areas (quads) for tympan k
        for e in range(ele_num):
            local_area_id, p1, p2, p3, p4 = ele_ij[e]
            area_id = elem_offset + int(local_area_id)

            g1 = node_offset + int(p1)
            g2 = node_offset + int(p2)
            g3 = node_offset + int(p3)
            g4 = node_offset + int(p4)

            # [AreaName, P1, P2, P3, P4, Section, Material]
            all_areas.append([
                str(area_id),
                str(g1), str(g2), str(g3), str(g4),
                "SHELL_200",   # section name (shell property)
                "CONC40",      # material name
            ])

    nodes_all = np.array(all_nodes, dtype=float)
    areas_all = np.array(all_areas, dtype=object)

    # We are using a pure-shell model; keep elements empty.
    elements_all = np.zeros((0, 5), dtype=object)

    return nodes_all, elements_all, areas_all
