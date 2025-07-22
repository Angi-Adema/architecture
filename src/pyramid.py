import numpy as np

# Eliminated the hardcoded values because in umbrella.py, when the user hits Run, the values entered by the user is dynamically passed into the geometry functions.

# Removed matplotlib, xlsxwriter, and 3D plotting as well as code for writing to Excel as these are now handled by the main GUI (umbrella.py). This separation of concerns makes pyramid.py a pure geometry generator with no UI or file responsibilities.

def pyramid(H, Re, Ne, N):

    psi = np.pi/Ne

    # Clearned up z-coordinate calculation to be more straightforward.
    def get_z(x):
        return (Re / H) * x

    nodes_tot = (N+1)**2
    x_base_i = np.linspace(0,H,N+1)
    x_i = np.zeros(nodes_tot)
    y_i = np.zeros(nodes_tot)
    z_i = np.zeros(nodes_tot)
    
    #Defining x and y coords. Got rid of loops with somewhat redundant logic and intermediate arrays like index_i. Keeps the triangular grid logic for a wedge/pyramid profile but avoids unused arrays and extra logic slowing the program down.
    n = 0
    for col in range(N+1):
        for i in range(N+1-col):
            x_i[n] = x_base_i[i+col]
            y_i[n] = x_base_i[col]*np.tan(psi)
            n += 1

    for col in range(N):
        for i in range(N-col):
            x_i[n] = x_base_i[i+1+col]
            y_i[n] = -x_base_i[col+1]*np.tan(psi)
            n += 1

    #Defining z coords
    for i in range(nodes_tot):
        z_i[i] = get_z(x_i[i])

    #Node and array creation simplified.
    nodes_ij = np.zeros((nodes_tot, 4))
    for i in range(nodes_tot):
        nodes_ij[i] = [i + 1, x_i[i], y_i[i], z_i[i]]

    ele_tot = N**2+N
    ele_ij = np.zeros((ele_tot,5))
    ele = 1

    # Had triangle vs. quadrilateral logic, with extra if statements and special cases. Still performs the same logic but does so more clearly and readably.
    for col in range(N):
        for i in range(N-col):
            ele_ij[ele-1][0] = ele
            if i == 0:
                ele_ij[ele-1][1] = ele + col
                ele_ij[ele-1][2] = ele_ij[ele-1][1] + (N+1) - col
                ele_ij[ele-1][3] = ele_ij[ele-1][1] + 1
                ele += 1
            else:
                ele_ij[ele-1][1] = ele + col
                ele_ij[ele-1][2] = ele_ij[ele-1][1] + N - col
                ele_ij[ele-1][3] = ele_ij[ele-1][2] + 1
                ele_ij[ele-1][4] = ele_ij[ele-1][1] + 1
                ele += 1

    for col in range(N):
        for i in range(N - col):
            ele_ij[ele - 1][0] = ele
            if i == 0:
                ele_ij[ele - 1][1] = ele + (N + 1)
                ele_ij[ele - 1][2] = 1 if col == 0 else ele + col
                ele_ij[ele - 1][3] = ele_ij[ele - 1][2] + 1
                ele += 1
            else:
                ele_ij[ele - 1][1] = ele + N
                ele_ij[ele - 1][2] = i + 1 if col == 0 else ele + col
                ele_ij[ele - 1][3] = ele_ij[ele - 1][2] + 1
                ele_ij[ele - 1][4] = ele + (N + 1)
                ele += 1
    
    # This keeps the function focused only on data computation, allowing umbrella.py to control file writing, previewing, and user interactions.
    return nodes_ij, ele_ij



# import matplotlib.pyplot as plt
# from mpl_toolkits.mplot3d import axes3d
# import csv
# import xlsxwriter

# H = 4.461
# Re = 0.96
# Ne = 12

# N = 20

    # def get_z(x):
    #     z = (Re/H)*x
    #     return z

    # nodes_ij = np.zeros((nodes_tot,4))
    # for i in range(nodes_tot):
    #     nodes_ij[i][0] = int(i + 1)
    #     nodes_ij[i][1] = x_i[i]
    #     nodes_ij[i][2] = y_i[i]
    #     nodes_ij[i][3] = z_i[i]

    # fig = plt.figure()
    # ax = fig.add_subplot(111,projection='3d')
    # ax.scatter(nodes_ij[:,1],nodes_ij[:,2],nodes_ij[:,3])
    # plt.tight_layout()
    # plt.show()

    # for col in range(N):
    #     for i in range(N-col):
    #         ele_ij[ele-1][0] = ele
    #         if i == 0: #Triangle
    #             ele_ij[ele-1][1] = ele + (N+1)
    #             if col == 0:
    #                 ele_ij[ele-1][2] = 1
    #             else:
    #                 ele_ij[ele-1][2] = ele + col
    #             ele_ij[ele-1][3] = ele_ij[ele-1][2] + 1
    #             ele = ele + 1
    #         else: #Quad
    #             ele_ij[ele-1][1] = ele + N
    #             if col == 0:
    #                 ele_ij[ele-1][2] = i + 1
    #             else:
    #                 ele_ij[ele-1][2] = ele + col
    #             ele_ij[ele-1][3] = ele_ij[ele-1][2] + 1
    #             ele_ij[ele-1][4] = ele + (N+1)
    #             ele = ele + 1

    # coords = xlsxwriter.Workbook('Pyramid'+str(Ne)+'_H'+str(H)+'_R'+str(Re)+'_N'+str(N)+'.xlsx')
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
