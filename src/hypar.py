import numpy as np

# Eliminated the hardcoded values because in umbrella.py, when the user hits Run,
# the values entered by the user are dynamically passed into the geometry functions.

def hypar(H, Re, Ne, N):
    """
    Generate a FULL hypar umbrella as:
      - nod_ij: [node_id, x, y, z] for all tympans
      - ele_ij: [frame_id, I, J, Section, Material] for a full grid of frames

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

    # ---------- geometry for ONE tympan ---------- #

    # base x-coordinates along the apothem
    xp_base_i = np.linspace(0, H, N + 1)
    xp_index_original = np.linspace(0, N, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot_single = int((N + 1) ** 2)

    xp_i = np.zeros(nodes_tot_single)
    index_i = np.zeros(nodes_tot_single)
    n = 0

    # Recreate original ordering from the source script
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

    # Compute z for each node
    z_i = np.array([get_z(xp_i[i], yp_i[i]) for i in range(nodes_tot_single)])

    # Build raw (x,y,z) before symmetry/rotation
    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),)
         for i in range(nodes_tot_single)]
    ).T  # shape (3, nodes_tot_single)

    # Mirror inside each column to fill the triangular patch
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                index = int(i + (N + 1) * col)
                xr, yr = sym(nodes_ij[0][index], nodes_ij[1][index])
                nodes_mod_ij[0][index] = xr
                nodes_mod_ij[1][index] = yr

    # Rotate the whole tympan to final orientation
    nodes_rot_ij = np.zeros((3, nodes_tot_single))
    for i in range(nodes_tot_single):
        x, y = rotate(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # Pack single-tympan nodes as [id, x, y, z]
    single_nodes = np.zeros((nodes_tot_single, 4))
    for n in range(nodes_tot_single):
        single_nodes[n] = [n + 1, nodes_rot_ij[0][n], nodes_rot_ij[1][n], nodes_rot_ij[2][n]]

    # Build FULL GRID of frame elements for ONE tympan
    # node_id = col * (N+1) + row + 1
    single_frames = []
    frame_id = 1

    # Horizontal frames (around the ring)
    for col in range(N):           # between column col and col+1
        for row in range(N + 1):   # all rows
            i_node = col * (N + 1) + row + 1
            j_node = (col + 1) * (N + 1) + row + 1
            single_frames.append([frame_id, i_node, j_node, 1, 1])
            frame_id += 1

    # Vertical frames (along the apothem)
    for col in range(N + 1):
        for row in range(N):       # between row and row+1
            i_node = col * (N + 1) + row + 1
            j_node = col * (N + 1) + row + 2
            single_frames.append([frame_id, i_node, j_node, 1, 1])
            frame_id += 1

    single_frames = np.array(single_frames, dtype=float)

    # ---------- replicate the tympan Ne times around Z (full umbrella) ---------- #

    all_nodes = []
    all_frames = []

    nodes_per_tympan = single_nodes.shape[0]
    frames_per_tympan = single_frames.shape[0]

    node_offset = 0
    frame_offset = 0

    for k in range(Ne):
        angle = 2.0 * np.pi * k / Ne
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)

        # Rotate nodes
        for row in single_nodes:
            old_id, x, y, z = row
            x_r = x * cos_a - y * sin_a
            y_r = x * sin_a + y * cos_a
            new_id = node_offset + int(old_id)
            all_nodes.append([new_id, x_r, y_r, z])

        # Replicate frames with updated node IDs
        for row in single_frames:
            old_fid, I, J, sec, mat = row
            new_fid = frame_offset + int(old_fid)
            new_I = node_offset + int(I)
            new_J = node_offset + int(J)
            all_frames.append([new_fid, new_I, new_J, sec, mat])

        node_offset += nodes_per_tympan
        frame_offset += frames_per_tympan

    nod_ij = np.array(all_nodes, dtype=float)
    ele_ij = np.array(all_frames, dtype=float)

    return nod_ij, ele_ij
