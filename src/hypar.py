# src/hypar.py
import numpy as np


def hypar(H, Re, Ne, N):
    """
    Generate ONE hypar tympan only (no radial replication here).

    Returns:
        nod_ij : ( (N+1)^2, 4 ) array
            [NodeID, X, Y, Z] for a single tympan.
        ele_ij : ( N^2, 5 ) array
            [ElemID, P1, P2, P3, P4] quad connectivity (node IDs).
    """
    psi_local = np.pi / Ne

    # ---------------- geometry helpers (single tympan) ---------------- #
    def get_z(xp, yp):
        delta_y = (H - xp) * np.sin(2 * psi_local) + xp * np.tan(psi_local)
        delta_x = (H - xp) * np.cos(2 * psi_local)
        z = (
            Re * (1 - xp / H) * yp / np.sqrt(delta_x**2 + delta_y**2)
            + Re * xp / H
        )
        return z

    def get_K(xp):
        delta_y = (H - xp) * np.sin(2 * psi_local) + xp * np.tan(psi_local)
        delta_x = (H - xp) * np.cos(2 * psi_local)
        psi_c = np.arctan(delta_x / delta_y)
        K = xp * np.sin(psi_local) / np.sin(np.pi / 2 - psi_local - psi_c)
        return K

    def get_xy(xp, yp):
        delta_y = (H - xp) * np.sin(2 * psi_local) + xp * np.tan(psi_local)
        delta_x = (H - xp) * np.cos(2 * psi_local)
        y = yp / np.sqrt(1 + (delta_x / delta_y) ** 2)
        x = xp + y * (delta_x / delta_y)
        return x, y

    def sym(x, y):
        theta = psi_local - np.arctan(y / x)
        xr = x * np.cos(2 * theta) - y * np.sin(2 * theta)
        yr = x * np.sin(2 * theta) + y * np.cos(2 * theta)
        return xr, yr

    def rotate(x, y, theta, n):
        xr = x * np.cos(theta * n) - y * np.sin(theta * n)
        yr = x * np.sin(theta * n) + y * np.cos(theta * n)
        return xr, yr

    # ---------------- node generation (same as your original) ---------------- #
    xp_base_i = np.linspace(0, H, N + 1)
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

    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),)
         for i in range(nodes_tot)]
    ).T

    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                idx = int(i + (N + 1) * col)
                xyr = sym(nodes_ij[0][idx], nodes_ij[1][idx])
                nodes_mod_ij[0][idx] = xyr[0]
                nodes_mod_ij[1][idx] = xyr[1]

    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = rotate(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi_local, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # ---------------- elements (quad connectivity for ONE tympan) ---------------- #
    ele_num = int(N**2)
    ele_ij = np.zeros((ele_num, 5), dtype=int)
    e = 0
    for col in range(N):
        for row in range(N):
            base = int(col + 1 + (N) * col + row)
            # [ElemID, P1, P2, P3, P4]
            ele_ij[e] = [e + 1, base, base + N + 1, base + N + 2, base + 1]
            e += 1

    # ---------------- pack nodes as [NodeID, X, Y, Z] ---------------- #
    nod_ij = np.zeros((nodes_tot, 4))
    for i in range(nodes_tot):
        nod_ij[i] = [
            i + 1,               # NodeID
            nodes_rot_ij[0][i],  # X
            nodes_rot_ij[1][i],  # Y
            nodes_rot_ij[2][i],  # Z
        ]

    return nod_ij, ele_ij