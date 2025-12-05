import numpy as np


# ------------------------------------------------------------
# 1) Original geometry for a SINGLE hypar tympan (one panel)
#    This is your existing logic, just moved into a helper.
#    It returns:
#       nod_ij: (nodes_tot, 4)  -> [Name, X, Y, Z]
#       ele_ij: (ele_num, 5)    -> [Frame, I, J, Section, Material]
# ------------------------------------------------------------
def _hypar_one_panel(H, Re, Ne, N):
    psi = np.pi / Ne

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
        theta = psi - np.arctan(y / x)
        xr = x * np.cos(2 * theta) - y * np.sin(2 * theta)
        yr = x * np.sin(2 * theta) + y * np.cos(2 * theta)
        return xr, yr

    def rotate_local(x, y, theta, n):
        xr = x * np.cos(theta * n) - y * np.sin(theta * n)
        yr = x * np.sin(theta * n) + y * np.cos(theta * n)
        return xr, yr

    # ------------- Generate panel nodes (same as your code) -------------
    xp_base_i = np.linspace(0, H, N + 1)
    xp_index_original = np.linspace(0, N, N + 1)  # kept for completeness
    xp_index_i = np.linspace(0, N, N + 1)
    nodes_tot = int((N + 1) ** 2)

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
            yp_i[n] = np.linspace(0, get_K(xp_i[n]), int(index_i[n] + 1))[int(index)]
            n += 1

    # z for each node
    z_i = np.array([get_z(xp_i[i], yp_i[i]) for i in range(nodes_tot)])

    # raw (x, y, z) before mirroring / rotation
    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),)
         for i in range(nodes_tot)]
    ).T  # shape (3, nodes_tot)

    # mirror half of the triangle (your sym() logic)
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                index = int(i + (N + 1) * col)
                xyr = sym(nodes_ij[0][index], nodes_ij[1][index])
                nodes_mod_ij[0][index] = xyr[0]
                nodes_mod_ij[1][index] = xyr[1]

    # rotate panel to final local orientation
    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = rotate_local(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # ------------- Build frame connectivity for ONE panel -------------
    ele_num = int(N**2)
    ele_ij = np.zeros((ele_num, 5), dtype=float)  # [Frame, I, J, Section, Material]
    n = 0

    # NOTE: This keeps your original indexing scheme.
    # Section / Material are placeholders (0 for now).
    for col in range(N):
        for row in range(N):
            base = int(col + 1 + N * col + row)
            ele_ij[n, 0] = n + 1          # Frame ID
            ele_ij[n, 1] = base           # I
            ele_ij[n, 2] = base + N + 1   # J
            ele_ij[n, 3] = 0              # Section (optional)
            ele_ij[n, 4] = 0              # Material (optional)
            n += 1

    # ------------- Pack node table: [Name, X, Y, Z] -------------
    nod_ij = np.zeros((nodes_tot, 4), dtype=float)
    for n in range(nodes_tot):
        nod_ij[n, 0] = n + 1
        nod_ij[n, 1] = nodes_rot_ij[0][n]
        nod_ij[n, 2] = nodes_rot_ij[1][n]
        nod_ij[n, 3] = nodes_rot_ij[2][n]

    return nod_ij, ele_ij


# ------------------------------------------------------------
# 2) New helper: rotate a point around the GLOBAL Z-axis
# ------------------------------------------------------------
def _rotate_global_z(x, y, z, theta):
    """
    Rotate (x, y, z) about the global Z-axis by angle 'theta' (radians).
    """
    xr = x * np.cos(theta) - y * np.sin(theta)
    yr = x * np.sin(theta) + y * np.cos(theta)
    return xr, yr, z


# ------------------------------------------------------------
# 3) Public hypar(H, Re, Ne, N): FULL umbrella
#
#    - Builds ONE panel using _hypar_one_panel
#    - Radially replicates it Ne times around the Z-axis
#    - Deduplicates joints at identical (x, y, z)
#    - Returns:
#         nodes_all: [Name, X, Y, Z] for entire umbrella
#         elems_all: [Frame, I, J, Section, Material] for entire umbrella
#
#    This matches what umbrella.py expects:
#         ret = hypar(H, Re, Ne, N)
#         nodes, elements, areas = _unpack_geometry(ret)
# ------------------------------------------------------------
def hypar(H, Re, Ne, N):
    # Generate single-panel geometry
    base_nodes, base_elems = _hypar_one_panel(H, Re, Ne, N)

    # We'll build the full umbrella here
    coord_to_id = {}      # (x,y,z) -> global node ID
    all_nodes = []        # [ID, X, Y, Z]
    all_elems = []        # [Frame, I, J, Section, Material]

    next_node_id = 1
    next_frame_id = 1

    # Pre-cast for convenience
    base_nodes = np.asarray(base_nodes, dtype=float)
    base_elems = np.asarray(base_elems, dtype=float)

    # Angular increment for each panel
    dtheta = 2.0 * np.pi / float(Ne)

    # --------- Replicate each panel around the Z-axis ---------
    for k in range(Ne):
        theta = k * dtheta

        # Map local node ID -> global node ID for this copy
        local_to_global = {}

        # ----- Nodes -----
        for row in base_nodes:
            local_id = int(row[0])
            x, y, z = float(row[1]), float(row[2]), float(row[3])

            xr, yr, zr = _rotate_global_z(x, y, z, theta)

            # key to merge coincident joints across panels
            key = (round(xr, 10), round(yr, 10), round(zr, 10))

            if key in coord_to_id:
                gid = coord_to_id[key]
            else:
                gid = next_node_id
                next_node_id += 1
                coord_to_id[key] = gid
                all_nodes.append([gid, xr, yr, zr])

            local_to_global[local_id] = gid

        # ----- Frames (elements) -----
        for row in base_elems:
            # base_elems: [Frame, I, J, Section, Material]
            local_I = int(row[1])
            local_J = int(row[2])
            sec = row[3]
            mat = row[4]

            global_I = local_to_global[local_I]
            global_J = local_to_global[local_J]

            all_elems.append([
                next_frame_id,
                global_I,
                global_J,
                sec,
                mat
            ])
            next_frame_id += 1

    # Convert back to numpy arrays (dtype=object to be safe with ints/floats)
    nodes_all = np.asarray(all_nodes, dtype=object)
    elems_all = np.asarray(all_elems, dtype=object)

    # We currently return NO Areas; umbrella.py will treat this as 2-tuple
    # and skip any area-related logic.
    return nodes_all, elems_all
