import numpy as np

# Eliminated the hardcoded values because in umbrella.py, when the user hits Run,
# the values entered by the user are dynamically passed into the geometry functions.

def hypar(H, Re, Ne, N):
    """
    Generate a FULL hypar umbrella as:
      - nod_ij: [node_id, x, y, z] for all tympans
      - ele_ij: [frame_id, I, J, Section, Material] for frame elements
      - areas_data: [AreaName, P1, P2, P3, P4, Section, Material] for shell areas

    H  = apothem length
    Re = rise
    Ne = number of sides (number of tympans around the umbrella)
    N  = number of elements along the apothem
    """

    psi = np.pi / Ne

    # ---------- helper functions for original hypar geometry ---------- #

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

    def rotate(x, y, theta, n):
        xr = x * np.cos(theta * n) - y * np.sin(theta * n)
        yr = x * np.sin(theta * n) + y * np.cos(theta * n)
        return xr, yr

    # ---------- geometry for ONE tympan (original indexing) ---------- #

    xp_base_i = np.linspace(0, H, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot_single = int((N + 1) ** 2)

    xp_i = np.zeros(nodes_tot_single)
    index_i = np.zeros(nodes_tot_single)
    n = 0

    # Reproduce the original column-based construction
    for col in range(N + 1):
        for i in range(col + 1):
            xp_base_i[i] = xp_base_i[int(xp_index_i[col])]
            xp_index_i[i] = int(col)
        for s in range(N + 1):
            xp_i[n] = xp_base_i[s]
            index_i[n] = xp_index_i[s]
            n += 1

    yp_i = np.zeros(nodes_tot_single)
    n = 0
    for col in range(N + 1):
        for i in range(N + 1):
            index = min(i, col)
            yp_i[n] = np.linspace(0, get_K(xp_i[n]), int(index_i[n] + 1))[int(index)]
            n += 1

    # (x,y,z) before symmetry
    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),)
         for i in range(nodes_tot_single)]
    ).T  # shape (3, nodes_tot_single)

    # Mirror inside each column
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                index = int(i + (N + 1) * col)
                xr, yr = sym(nodes_ij[0][index], nodes_ij[1][index])
                nodes_mod_ij[0][index] = xr
                nodes_mod_ij[1][index] = yr

    # Rotate tympan to final orientation
    nodes_rot_ij = np.zeros((3, nodes_tot_single))
    for i in range(nodes_tot_single):
        x, y = rotate(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # Pack single-tympan nodes as [id, x, y, z]
    single_nodes = np.zeros((nodes_tot_single, 4))
    for n in range(nodes_tot_single):
        single_nodes[n] = [n + 1, nodes_rot_ij[0][n], nodes_rot_ij[1][n], nodes_rot_ij[2][n]]

    # ---- original quad indexing for ONE tympan (area mesh) ---- #
    ele_num_single = int(N ** 2)
    single_quads = np.zeros((ele_num_single, 5), dtype=int)
    n = 0
    for col in range(N):
        for row in range(N):
            # This matches your original ele_ij indexing
            p1 = int(col + 1 + (N) * col + row)
            p2 = int(p1 + N + 1)
            p3 = int(p2 + 1)
            p4 = int(p1 + 1)

            single_quads[n, 0] = n + 1   # area/element ID (within this tympan)
            single_quads[n, 1] = p1
            single_quads[n, 2] = p2
            single_quads[n, 3] = p3
            single_quads[n, 4] = p4
            n += 1

    # ---------- replicate tympan Ne times around Z (nodes + quads) ---------- #

    all_nodes = []
    all_quads = []

    nodes_per_tympan = single_nodes.shape[0]
    quads_per_tympan = single_quads.shape[0]

    node_offset = 0
    quad_offset = 0

    for k in range(Ne):
        angle = 2.0 * np.pi * k / Ne
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)

        # Rotate nodes for tympan k
        for row in single_nodes:
            old_id, x, y, z = row
            x_r = x * cos_a - y * sin_a
            y_r = x * sin_a + y * cos_a
            new_id = node_offset + int(old_id)
            all_nodes.append([new_id, x_r, y_r, z])

        # Replicate quads with updated node IDs
        for row in single_quads:
            local_qid, p1, p2, p3, p4 = row
            new_qid = quad_offset + local_qid
            new_p1 = node_offset + p1
            new_p2 = node_offset + p2
            new_p3 = node_offset + p3
            new_p4 = node_offset + p4
            all_quads.append([new_qid, new_p1, new_p2, new_p3, new_p4])

        node_offset += nodes_per_tympan
        quad_offset += quads_per_tympan

    nod_ij = np.array(all_nodes, dtype=float)
    all_quads = np.array(all_quads, dtype=int)

    # ---------- build UNIQUE frame elements from quad edges ---------- #

    edges = set()
    for _, p1, p2, p3, p4 in all_quads:
        quad_nodes = [int(p1), int(p2), int(p3), int(p4)]
        for i in range(4):
            a = quad_nodes[i]
            b = quad_nodes[(i + 1) % 4]
            if a == b:
                continue
            key = tuple(sorted((a, b)))
            edges.add(key)

    edges = sorted(edges)
    ele_ij = np.zeros((len(edges), 5), dtype=float)
    for idx, (i_node, j_node) in enumerate(edges, start=1):
        ele_ij[idx - 1] = [idx, i_node, j_node, 1, 1]  # Section=1, Material=1 (placeholders)

    # ---------- Areas data for SAP2000 (for shell mesh) ---------- #
    # Format: [AreaName, P1, P2, P3, P4, Section, Material]
