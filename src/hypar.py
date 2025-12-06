import numpy as np


def hypar(H, Re, Ne, N):
    """
    Generate a hypar umbrella (Ne tympans) with:
      - nodes_ij: [Name, X, Y, Z]
      - elems_ij: [Frame, I, J, Section, Material]
      - areas_ij: [Area, P1, P2, P3, P4, Section, Material]

    H   = apothem length
    Re  = rise
    Ne  = number of sides (tym­pans)
    N   = number of divisions along the apothem
    """

    psi = np.pi / Ne  # half central angle

    # ---- geometry helpers (same as your original logic) ----
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
        theta = psi - np.arctan2(y, x)
        xr = x * np.cos(2 * theta) - y * np.sin(2 * theta)
        yr = x * np.sin(2 * theta) + y * np.cos(2 * theta)
        return xr, yr

    def rotate_xy(x, y, theta):
        """Rotate (x, y) in the XY-plane by angle theta."""
        xr = x * np.cos(theta) - y * np.sin(theta)
        yr = x * np.sin(theta) + y * np.cos(theta)
        return xr, yr

    # --------------------------------------------------------
    # 1) Generate ONE tympan (same as legacy code)
    # --------------------------------------------------------
    xp_base_i = np.linspace(0, H, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot = (N + 1) ** 2
    xp_i = np.zeros(nodes_tot)
    index_i = np.zeros(nodes_tot)

    n = 0
    for col in range(N + 1):
        # copy the base values for this column
        for i in range(col + 1):
            xp_base_i[i] = xp_base_i[int(xp_index_i[col])]
            xp_index_i[i] = int(col)
        for s in range(N + 1):
            xp_i[n] = xp_base_i[s]
            index_i[n] = xp_index_i[s]
            n += 1

    # yp_i
    yp_i = np.zeros(nodes_tot)
    n = 0
    for col in range(N + 1):
        for i in range(N + 1):
            idx = min(i, col)
            K_val = get_K(xp_i[n])
            yp_i[n] = np.linspace(0, K_val, int(index_i[n] + 1))[int(idx)]
            n += 1

    # Base tympan z, x, y
    z_i = np.array([get_z(xp_i[i], yp_i[i]) for i in range(nodes_tot)])
    nodes_ij = np.array([get_xy(xp_i[i], yp_i[i]) + (z_i[i],) for i in range(nodes_tot)]).T

    # Mirror for the “diamond” pattern (same as original)
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                index = int(i + (N + 1) * col)
                x_m, y_m = sym(nodes_ij[0][index], nodes_ij[1][index])
                nodes_mod_ij[0][index] = x_m
                nodes_mod_ij[1][index] = y_m

    # Rotate so that local apex orientation matches your manual model
    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x_r, y_r = rotate_xy(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi)
        nodes_rot_ij[0][i] = x_r
        nodes_rot_ij[1][i] = y_r
        nodes_rot_ij[2][i] = nodes_mod_ij[2][i]

    # --------------------------------------------------------
    # 2) Frame grid for ONE tympan (2 * N * (N + 1) beams)
    #    We'll use indices 1..nodes_tot as in the spreadsheet.
    # --------------------------------------------------------
    frame_list = []
    # helper to convert (row, col) -> node index
    def node_id(r, c):
        # r, c in [0..N]
        return r * (N + 1) + c + 1  # +1 for 1-based

    frame_id = 1
    # along "rows" (fix r, vary c)
    for r in range(N + 1):
        for c in range(N):
            ipt = node_id(r, c)
            jpt = node_id(r, c + 1)
            frame_list.append([frame_id, ipt, jpt, 1, 1])  # Section=1, Material=1 placeholder
            frame_id += 1

    # along "columns" (fix c, vary r)
    for c in range(N + 1):
        for r in range(N):
            ipt = node_id(r, c)
            jpt = node_id(r + 1, c)
            frame_list.append([frame_id, ipt, jpt, 1, 1])
            frame_id += 1

    ele_one = np.array(frame_list, dtype=float)
    frames_per_tympan = ele_one.shape[0]

    # --------------------------------------------------------
    # 3) Area (shell) elements for ONE tympan: N × N quads
    # --------------------------------------------------------
    area_list = []
    area_id = 1
    for r in range(N):
        for c in range(N):
            n1 = node_id(r, c)
            n2 = node_id(r, c + 1)
            n3 = node_id(r + 1, c + 1)
            n4 = node_id(r + 1, c)
            area_list.append([area_id, n1, n2, n3, n4, 1, 1])  # Section=1, Material=1
            area_id += 1

    areas_one = np.array(area_list, dtype=float)
    areas_per_tympan = areas_one.shape[0]

    # --------------------------------------------------------
    # 4) Node table for ONE tympan (IDs 1..nodes_tot)
    # --------------------------------------------------------
    nod_one = np.zeros((nodes_tot, 4), dtype=float)
    for i in range(nodes_tot):
        nod_one[i, 0] = i + 1
        nod_one[i, 1] = nodes_rot_ij[0, i]
        nod_one[i, 2] = nodes_rot_ij[1, i]
        nod_one[i, 3] = nodes_rot_ij[2, i]

    # --------------------------------------------------------
    # 5) Replicate around Z-axis Ne times, with consistent offsets
    # --------------------------------------------------------
    all_nodes = []
    all_frames = []
    all_areas = []

    nodes_per_tympan = nodes_tot

    for k in range(Ne):
        angle = 2.0 * psi * k  # 0, 2ψ, 4ψ, ...  = 0, 90°, 180°, 270° for Ne=4
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)

        # --- nodes ---
        for i in range(nodes_per_tympan):
            base_id = int(nod_one[i, 0])
            x0 = nod_one[i, 1]
            y0 = nod_one[i, 2]
            z0 = nod_one[i, 3]

            xr = x0 * cos_a - y0 * sin_a
            yr = x0 * sin_a + y0 * cos_a

            new_id = base_id + k * nodes_per_tympan
            all_nodes.append([new_id, xr, yr, z0])

        # --- frames ---
        for f in range(frames_per_tympan):
            base_fid = int(ele_one[f, 0])
            i_pt = int(ele_one[f, 1])
            j_pt = int(ele_one[f, 2])
            sec = ele_one[f, 3]
            mat = ele_one[f, 4]

            new_fid = base_fid + k * frames_per_tympan
            i_new = i_pt + k * nodes_per_tympan
            j_new = j_pt + k * nodes_per_tympan

            all_frames.append([new_fid, i_new, j_new, sec, mat])

        # --- areas ---
        for a in range(areas_per_tympan):
            base_aid = int(areas_one[a, 0])
            p1 = int(areas_one[a, 1])
            p2 = int(areas_one[a, 2])
            p3 = int(areas_one[a, 3])
            p4 = int(areas_one[a, 4])
            sec_a = areas_one[a, 5]
            mat_a = areas_one[a, 6]

            new_aid = base_aid + k * areas_per_tympan
            p1_new = p1 + k * nodes_per_tympan
            p2_new = p2 + k * nodes_per_tympan
            p3_new = p3 + k * nodes_per_tympan
            p4_new = p4 + k * nodes_per_tympan

            all_areas.append([new_aid, p1_new, p2_new, p3_new, p4_new, sec_a, mat_a])

    nod_all = np.array(all_nodes, dtype=float)
    ele_all = np.array(all_frames, dtype=float)
    areas_all = np.array(all_areas, dtype=float)

    return nod_all, ele_all, areas_all