import numpy as np

def hypar(H, Re, Ne, N):
    """
    Generate a single hypar tympan as:
      - nod_ij: [node_id, x, y, z] for (N+1)^2 nodes
      - ele_ij: [frame_id, I, J, Section, Material] for a full grid of frames

    H  = apothem length
    Re = rise
    Ne = number of sides of the umbrella (not used inside here yet, but
         kept in the signature for consistency with pyramid/dome)
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

    # ---------- geometry generation for a SINGLE tympan ---------- #

    # base x-coordinates along the apothem
    xp_base_i = np.linspace(0, H, N + 1)
    xp_index_original = np.linspace(0, N, N + 1)
    xp_index_i = np.linspace(0, N, N + 1)

    nodes_tot = int((N + 1) ** 2)

    xp_i = np.zeros(nodes_tot)
    index_i = np.zeros(nodes_tot)
    n = 0

    # This recreates the original node ordering from the source script
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

    # Compute z for each node
    z_i = np.array([get_z(xp_i[i], yp_i[i]) for i in range(nodes_tot)])

    # Build raw (x,y,z) before symmetry/rotation
    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),)
         for i in range(nodes_tot)]
    ).T  # shape (3, nodes_tot)

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
    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = rotate(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # ---------- pack nodes as [id, x, y, z] ---------- #

    nod_ij = np.zeros((nodes_tot, 4))
    for n in range(nodes_tot):
        nod_ij[n] = [n + 1, nodes_rot_ij[0][n], nodes_rot_ij[1][n], nodes_rot_ij[2][n]]

    # ---------- build FULL GRID of frame elements (Option A) ---------- #
    #
    # We treat the nod_ij indexing as a (N+1) x (N+1) grid:
    #   node_id = col * (N+1) + row + 1
    # where:
    #   col = 0..N (around the ring)
    #   row = 0..N (along the apothem)
    #
    # Horizontal frames: connect (col,row) -> (col+1,row)
    # Vertical frames:   connect (col,row) -> (col,row+1)

    frames = []
    frame_id = 1

    # Horizontal frames (around the ring)
    for col in range(N):           # between column col and col+1
        for row in range(N + 1):   # all rows
            i_node = col * (N + 1) + row + 1
            j_node = (col + 1) * (N + 1) + row + 1
            frames.append([frame_id, i_node, j_node, 1, 1])
            frame_id += 1

    # Vertical frames (along the apothem)
    for col in range(N + 1):
        for row in range(N):       # between row and row+1
            i_node = col * (N + 1) + row + 1
            j_node = col * (N + 1) + row + 2
            frames.append([frame_id, i_node, j_node, 1, 1])
            frame_id += 1

    ele_ij = np.array(frames, dtype=float)

    # nod_ij and ele_ij are now exported by umbrella.py
    # (umbrella writes Nodes & Elements sheets; SAP2000 treats Elements as frames)
    return nod_ij, ele_ij