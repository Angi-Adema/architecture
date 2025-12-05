import numpy as np

# Eliminated the hardcoded values because in umbrella.py, when the user hits Run,
# the values entered by the user are dynamically passed into the geometry functions.

def hypar(H, Re, Ne, N):
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

    def rotate(x, y, theta, n):
        xr = x * np.cos(theta * n) - y * np.sin(theta * n)
        yr = x * np.sin(theta * n) + y * np.cos(theta * n)
        return xr, yr

    # ----- Geometry generation -----
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

    # Compute XYZ for all nodes
    nodes_ij = np.array(
        [get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),)
         for i in range(nodes_tot)]
    ).T  # shape (3, nodes_tot)

    # Mirror where needed
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                index = int(i + (N + 1) * col)
                xyr = sym(nodes_ij[0][index], nodes_ij[1][index])
                nodes_mod_ij[0][index] = xyr[0]
                nodes_mod_ij[1][index] = xyr[1]

    # Rotate whole patch
    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = rotate(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    # ----- Nodes array: [Name, X, Y, Z] -----
    nod_ij = np.zeros((nodes_tot, 4), dtype=float)
    for n in range(nodes_tot):
        nod_ij[n] = [
            n + 1,                     # Name
            nodes_rot_ij[0][n],        # X
            nodes_rot_ij[1][n],        # Y
            nodes_rot_ij[2][n],        # Z
        ]

    # ----- Original quad connectivity (local to this function) -----
    ele_num = int(N ** 2)
    quad_ij = np.zeros((ele_num, 5), dtype=float)  # [id, n1, n2, n3, n4]
    n = 0
    for col in range(N):
        for row in range(N):
            base = int(col + 1 + N * col + row)
            quad_ij[n] = [n + 1, base, base + N + 1, base + N + 2, base + 1]
            n += 1

    # ----- Areas sheet data: one quad per cell -----
    # Format expected by umbrella/sap_integration:
    #   [AreaName, P1, P2, P3, P4, Section, Material]
    areas = []
    for row in quad_ij:
        _, n1, n2, n3, n4 = row.astype(int)
        areas.append([
            int(len(areas) + 1),  # AreaName
            int(n1),
            int(n2),
            int(n3),
            int(n4),
            "",                    # Section (optional, left blank)
            "",                    # Material (optional, left blank)
        ])

    # ----- Frame elements (grid lines) -----
    # Build all edges of all quads, then deduplicate:
    # format: [Frame, I, J, Section, Material]
    edges = set()
    for row in quad_ij:
        _, n1, n2, n3, n4 = row.astype(int)
        for (i, j) in [(n1, n2), (n2, n3), (n3, n4), (n4, n1)]:
            key = tuple(sorted((int(i), int(j))))
            edges.add(key)

    elements = []
    frame_id = 1
    for (i, j) in sorted(edges):
        elements.append([frame_id, i, j, "", ""])
        frame_id += 1

    elements_arr = np.array(elements, dtype=object)

    # Return nodes, frame elements, and area quads
    return nod_ij, elements_arr, areas
