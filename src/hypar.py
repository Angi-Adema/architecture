import numpy as np

# Eliminated the hardcoded values because in umbrella.py, when the user hits Run, the values entered by the user is dynamically passed into the geometry functions.

def hypar(H, Re, Ne, N):

    psi = np.pi/Ne

    def get_z(xp,yp):
        delta_y = (H - xp)*np.sin(2*psi) + xp*np.tan(psi)
        delta_x = (H - xp)*np.cos(2*psi)
        z = Re*(1 - xp/H)*yp/np.sqrt(delta_x**2 + delta_y**2) + Re*xp/H
        return z

    def get_K(xp):
        delta_y = (H - xp)*np.sin(2*psi) + xp*np.tan(psi)
        delta_x = (H - xp)*np.cos(2*psi)
        psi_c = np.arctan(delta_x/delta_y)
        K = xp*np.sin(psi)/np.sin(np.pi/2 - psi - psi_c)
        return K

    def get_xy(xp,yp):
        delta_y = (H - xp)*np.sin(2*psi) + xp*np.tan(psi)
        delta_x = (H - xp)*np.cos(2*psi)
        y = yp/np.sqrt(1 + (delta_x/delta_y)**2)
        x = xp + y*(delta_x/delta_y)
        return x, y

    def sym(x,y):
        theta = psi - np.arctan(y/x)
        xr = x*np.cos(2*theta) - y*np.sin(2*theta)
        yr = x*np.sin(2*theta) + y*np.cos(2*theta)
        return xr, yr

    def rotate(x,y,theta,n):
        xr = x*np.cos(theta*n) - y*np.sin(theta*n)
        yr = x*np.sin(theta*n) + y*np.cos(theta*n)
        return xr, yr

    # -- Geometry generation --
    xp_base_i = np.linspace(0,H,N+1)
    xp_index_original = np.linspace(0,N,N+1)
    xp_index_i = np.linspace(0,N,N+1)
    nodes_tot = int((N+1)**2)
    xp_i = np.zeros(nodes_tot)
    index_i = np.zeros(nodes_tot)
    n = 0

    for col in range(N+1):
        for i in range(col+1):
            xp_base_i[i] = xp_base_i[int(xp_index_i[col])]
            xp_index_i[i] = int(col)
        for s in range(N+1):
            xp_i[n] = xp_base_i[s]
            index_i[n] = xp_index_i[s]
            n += 1

    yp_i = np.zeros(nodes_tot)
    n = 0
    for col in range(N+1):
        for i in range(N+1):
            index = min(i, col)
            yp_i[n] = np.linspace(0,get_K(xp_i[n]),int(index_i[n]+1))[int(index)]
            n += 1

    # List comprehensions are often more readable and slightly faster than basic for loops.
    z_i = np.array([get_z(xp_i[i], yp_i[i]) for i in range(nodes_tot)])

    # This part separates the steps, compute xp_i and yp_i arrays for raw coordinates, then call get_xy() and get_z() to get actual x, y, z for each node then create a clean nodes_ij array.
    nodes_ij = np.array([get_xy(xp_i[i], yp_i[i]) + (get_z(xp_i[i], yp_i[i]),) for i in range(nodes_tot)]).T

    # Here we kept sym() for mirroring, rotate() for final orientation and nodes_mod_ij for intermediate transformation steps but now it is all separated from drawing/export logic making it more modular.
    nodes_mod_ij = np.copy(nodes_ij)
    for col in range(N + 1):
        for i in range(N + 1):
            if i < col:
                index = int(i + (N + 1) * col)
                xyr = sym(nodes_ij[0][index], nodes_ij[1][index])
                nodes_mod_ij[0][index] = xyr[0]
                nodes_mod_ij[1][index] = xyr[1]

    nodes_rot_ij = np.zeros((3, nodes_tot))
    for i in range(nodes_tot):
        x, y = rotate(nodes_mod_ij[0][i], nodes_mod_ij[1][i], -psi, 1)
        nodes_rot_ij[:, i] = [x, y, nodes_mod_ij[2][i]]

    ele_num = int(N ** 2)
    ele_ij = np.zeros((ele_num, 5))
    n = 0
    for col in range(N):
        for row in range(N):
            base = int(col + 1 + (N) * col + row)
            ele_ij[n] = [n + 1, base, base + N + 1, base + N + 2, base + 1]
            n += 1

    nod_ij = np.zeros((nodes_tot, 4))
    for n in range(nodes_tot):
        nod_ij[n] = [n + 1, nodes_rot_ij[0][n], nodes_rot_ij[1][n], nodes_rot_ij[2][n]]

    # nod_ij and ele_ij are now exported using umbrella.py keeping hypar.py focused purely on geometry logic. worksheet.write() was removed as it is hardcoded and should be dynamic.
    return nod_ij, ele_ij



# import matplotlib.pyplot as plt
# from mpl_toolkits.mplot3d import axes3d
# import csv
# import xlsxwriter

# H = 9
# Re = 2
# Ne = 6

# N = 21

    # z_i = np.zeros(nodes_tot)
    # for i in range(nodes_tot):
    #     z_i[i] = get_z(xp_i[i],yp_i[i])

    # nodes_mod_ij = nodes_ij
    # for col in range(N+1):
    #     for i in range(N+1):
    #         if i < col:
    #             index = int(i + (N+1)*col)
    #             xyr = sym(nodes_ij[0][index],nodes_ij[1][index])
    #             nodes_mod_ij[0][index] = xyr[0]
    #             nodes_mod_ij[1][index] = xyr[1]

    # nodes_rot_ij = np.zeros((3,nodes_tot))
    # for i in range(nodes_tot):
    #     nodes_rot_ij[0][i] = rotate(nodes_mod_ij[0][i],nodes_mod_ij[1][i],-psi,1)[0]
    #     nodes_rot_ij[1][i] = rotate(nodes_mod_ij[0][i],nodes_mod_ij[1][i],-psi,1)[1]
    #     nodes_rot_ij[2][i] = nodes_mod_ij[2][i]

    # fig = plt.figure()
    # ax = fig.add_subplot(111,projection='3d')
    # ax.scatter(nodes_rot_ij[0],nodes_rot_ij[1],nodes_rot_ij[2])
    
    # ax.set_xlim([-H,H])
    # ax.set_ylim([-H,H])
    # ax.set_zlim([-H,H])
    # for i in range(Ne):
        # rot_tym_ij = np.zeros((3,nodes_tot))
        # for nodes in range(nodes_tot):
            # rot_tym_ij[0][nodes] = rotate(nodes_rot_ij[0][nodes],nodes_rot_ij[1][nodes],2*psi,i+1)[0]
            # rot_tym_ij[1][nodes] = rotate(nodes_rot_ij[0][nodes],nodes_rot_ij[1][nodes],2*psi,i+1)[1]
        # ax.scatter(rot_tym_ij[0],rot_tym_ij[1],nodes_ij[2])
    
    # plt.tight_layout()
    # plt.show()

    # ele_num = int(N**2)
    # ele_ij = np.zeros((ele_num,5))
    # n = 0
    # for col in range(N):
    #     for row in range(N):
    #         ele_ij[n][0] = int(n+1)
    #         ele_ij[n][1] = int(col + 1 +(N)*col + row)
    #         ele_ij[n][2] = int(ele_ij[n][1] + N + 1)
    #         ele_ij[n][3] = int(ele_ij[n][2] + 1)
    #         ele_ij[n][4] = int(ele_ij[n][1] + 1)
    #         n = n + 1

    # nod_ij = np.zeros((nodes_tot,4))
    # for n in range(nodes_tot):
    #     nod_ij[n][0] = int(n+1)
    #     nod_ij[n][1] = nodes_rot_ij[0][n]
    #     nod_ij[n][2] = nodes_rot_ij[1][n]
    #     nod_ij[n][3] = nodes_rot_ij[2][n]

    # coords = xlsxwriter.Workbook('Hypar'+str(Ne)+'_H'+str(H)+'_R'+str(Re)+'_N'+str(N)+'.xlsx')
    # worksheet = coords.add_worksheet('Nodes')
    # for i in range(nodes_tot):
        # worksheet.write('A%d' % (i+1), nod_ij[i][0])
        # worksheet.write('D%d' % (i+1), nod_ij[i][1])
        # worksheet.write('E%d' % (i+1), nod_ij[i][2])
        # worksheet.write('G%d' % (i+1), nod_ij[i][3])
    # worksheet2 = coords.add_worksheet('Elements')
    # for i in range(ele_num):
        # worksheet2.write('A%d' % (i+1), ele_ij[i][0])
        # worksheet2.write('B%d' % (i+1), ele_ij[i][1])
        # worksheet2.write('C%d' % (i+1), ele_ij[i][2])
        # worksheet2.write('D%d' % (i+1), ele_ij[i][3])
        # worksheet2.write('E%d' % (i+1), ele_ij[i][4])
    # coords.close()