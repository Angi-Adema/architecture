import numpy as np
# import matplotlib.pyplot as plt
# from mpl_toolkits.mplot3d import axes3d
# import csv
# import xlsxwriter

H = 4.298
Re = 1.98
Ne = 6

N = 20

def dome(H, Re, Ne, N):

    psi = np.pi/Ne

    def get_z(x,y):
        z = (Re*np.cos(psi)**2/H**2)*(x**2+y**2)
        return z

    nodes_tot = int((N+1)**2)
    x_base_i = np.linspace(0,H,N+1)
    x_i = np.zeros(nodes_tot)
    y_i = np.zeros(nodes_tot)
    z_i = np.zeros(nodes_tot)
    index_i = np.zeros(nodes_tot)
    #Defining x and y coords
    n = 0
    for col in range(N+1):
        for i in range(N+1-col):
            x_i[n] = x_base_i[i+col]
            y_i[n] = x_base_i[col]*np.tan(psi)
            n = n + 1
    for col in range(N):
        for i in range(N-col):
            x_i[n] = x_base_i[i+1+col]
            y_i[n] = -x_base_i[col+1]*np.tan(psi)
            n = n + 1
    #Defining z coords
    for i in range(nodes_tot):
        z_i[i] = get_z(x_i[i],y_i[i])

    #Creating nodes array
    nodes_ij = np.zeros((nodes_tot,4))
    for i in range(nodes_tot):
        nodes_ij[i][0] = int(i + 1)
        nodes_ij[i][1] = x_i[i]
        nodes_ij[i][2] = y_i[i]
        nodes_ij[i][3] = z_i[i]

    # fig = plt.figure()
    # ax = fig.add_subplot(111,projection='3d')
    # ax.set_xlim([-H,H])
    # ax.set_ylim([-H,H])
    # ax.set_zlim([-H,H])
    # ax.scatter(nodes_ij[:,1],nodes_ij[:,2],nodes_ij[:,3])
    # plt.tight_layout()
    # plt.show()

    ele_tot = int(N**2+N)
    ele_ij = np.zeros((ele_tot,5))
    ele = 1
    for col in range(N):
        for i in range(N-col):
            ele_ij[ele-1][0] = ele
            if i == 0:
                ele_ij[ele-1][1] = ele + col
                ele_ij[ele-1][2] = ele_ij[ele-1][1] + (N+1) - col
                ele_ij[ele-1][3] = ele_ij[ele-1][1] + 1
                ele = ele + 1
            else:
                ele_ij[ele-1][1] = ele + col
                ele_ij[ele-1][2] = ele_ij[ele-1][1] + (N) - col
                ele_ij[ele-1][3] = ele_ij[ele-1][2] + 1
                ele_ij[ele-1][4] = ele_ij[ele-1][1] + 1
                ele = ele + 1
    for col in range(N):
        for i in range(N-col):
            ele_ij[ele-1][0] = ele
            if i == 0: #Triangle
                ele_ij[ele-1][1] = ele + (N+1)
                if col == 0:
                    ele_ij[ele-1][2] = 1
                else:
                    ele_ij[ele-1][2] = ele + col
                ele_ij[ele-1][3] = ele_ij[ele-1][2] + 1
                ele = ele + 1
            else: #Quad
                ele_ij[ele-1][1] = ele + N
                if col == 0:
                    ele_ij[ele-1][2] = i + 1
                else:
                    ele_ij[ele-1][2] = ele + col
                ele_ij[ele-1][3] = ele_ij[ele-1][2] + 1
                ele_ij[ele-1][4] = ele + (N+1)
                ele = ele + 1

    # coords = xlsxwriter.Workbook('Parabola'+str(Ne)+'_H'+str(H)+'_R'+str(Re)+'_N'+str(N)+'.xlsx')
    # worksheet = coords.add_worksheet('Nodes')
    # for i in range(nodes_tot):
        # worksheet.write('A%d' % (i+1), nodes_ij[i][0])
        # worksheet.write('D%d' % (i+1), nodes_ij[i][1])
        # worksheet.write('E%d' % (i+1), nodes_ij[i][2])
        # worksheet.write('G%d' % (i+1), nodes_ij[i][3])
    # worksheet2 = coords.add_worksheet('Elements')
    # for i in range(ele_tot):
        # worksheet2.write('A%d' % (i+1), ele_ij[i][0])
        # worksheet2.write('B%d' % (i+1), ele_ij[i][1])
        # worksheet2.write('C%d' % (i+1), ele_ij[i][2])
        # worksheet2.write('D%d' % (i+1), ele_ij[i][3])
        # worksheet2.write('E%d' % (i+1), ele_ij[i][4])
    # coords.close()
    
    return nodes_ij, ele_ij