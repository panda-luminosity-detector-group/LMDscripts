#!/usr/bin/env python3

import numpy as np
import uproot, os, sys, argparse, json
from trksQA import getIPfromTrksQA
from matrices import getMatrixFromJSON, makeHomogenous

"""
Author: R. Klasen, roklasen@uni-mainz.de or r.klasen@gsi.de

read TrksQA.root files
find apparent IP position
compare with actual IP position from PANDA upstream

save matrix to json file
rerun Reco and Lumi steps

- FIXME: unify vector handling. most vectors are still row-major and not homogenous!

- TODO: sacn multiple directories for TrksQA, parse align factor, compute box rotation matrices and store to pandaroot dir! 
"""

def getLumiPosition():

    if False:
        # get values from survey!
        lumiPos = np.array([0.0, 0.0, 0.0, 1.0])[np.newaxis].T
        lumiMat = getMatrixFromJSON('../input/rootMisalignMatrices/json/detectorMatrices.json', '/cave_1/lmd_root_0')
        newLumiPos = (lumiMat@lumiPos).T[0][:3]
        return newLumiPos
    else:
        # values are pre-calculated for ideal lumi position
        return np.array((25.37812835, 0.0, 1109.13))

# https://stackoverflow.com/questions/15022630/how-to-calculate-the-angle-from-rotation-matrix
def getEulerAnglesFromRotationMatrix(R):
    rx = np.arctan2(R[2][1], R[2][2])
    ry = np.arctan2(-R[2][0], np.sqrt(R[2][1]*R[2][1] + R[2][2] * R[2][2]))
    rz = np.arctan2(R[1][0], R[0][0])
    return (rx, ry, rz)


def printMatrixDetails(M1, M2=None):

    if M2 is not None:
        rx1, ry1, rz1 = getEulerAnglesFromRotationMatrix(M1)
        rx2, ry2, rz2 = getEulerAnglesFromRotationMatrix(M2)
        rx = rx1 - rx2
        ry = ry1 - ry2
        rz = rz1 - rz2
        M = M1 - M2
        print(f'M Difference:\n{M}')
    else:
        rx, ry, rz = getEulerAnglesFromRotationMatrix(M1)
        M = M1
        print(f'M:\n{M}')

    print(f'angle x: {rx * 1e3} mrad')
    print(f'angle y: {ry * 1e3} mrad')
    print(f'angle z: {rz * 1e3} mrad')
    print('\n')


# see https://math.stackexchange.com/a/476311
# https://en.wikipedia.org/wiki/Cross_product
"""
computes rotation from A to B when rotated through origin.
shift A and B before, if rotation did not already occur through origin!
"""
def getRot(apparent, actual):
    # error handling
    if np.linalg.norm(apparent) == 0 or np.linalg.norm(actual) == 0:
        print("ERROR. can't create rotation with null vector")
        return

    # assert shapes
    assert apparent.shape == actual.shape

    # normalize vectors
    apparent = apparent / np.linalg.norm(apparent)
    actual = actual / np.linalg.norm(actual)

    # calc rot angle by dot product
    cosine = np.dot(apparent, actual)  # cosine

    # make 2D vectors so that transposing works
    cvector = apparent[np.newaxis].T
    dvector = actual[np.newaxis].T

    # compute skew symmetric cross product matrix
    crossMatrix = np.matmul(dvector, cvector.T) - np.matmul(cvector, dvector.T)

    # compute rotation matrix
    R = np.identity(3) + crossMatrix + np.dot(crossMatrix, crossMatrix) * (1/(1+cosine))

    return R

def getRotWiki(apparent, actual):

    # error handling
    if np.linalg.norm(apparent) == 0 or np.linalg.norm(actual) == 0:
        print("ERROR. can't create rotation with null vector")
        return

    # assert shapes
    assert apparent.shape == actual.shape

    # calc rot axis
    axis = np.cross(apparent, actual)
    axisnorm = np.linalg.norm(axis)
    axisN = axis / axisnorm

    # normalize vectors
    apparentnorm = np.linalg.norm(apparent)
    actualnorm = np.linalg.norm(actual)

    # calc rot angle by dot product
    cos = np.dot(apparent, actual) / (apparentnorm * actualnorm)  # cosine
    sin = axisnorm / (apparentnorm * actualnorm)

    ux = axisN[0]
    uy = axisN[1]
    uz = axisN[2]

    R1 = [[
        cos+ux*ux*(1-cos),      ux*uy*(1-cos)-uz*sin,   ux*uz*(1-cos)+uy*sin    ],[
        uy*ux*(1-cos)+uz*sin,   cos+uy*uy*(1-cos),      uy*uz*(1-cos)-ux*sin    ],[
        uz*ux*(1-cos)-uy*sin,   uz*uy*(1-cos)+ux*sin,   cos+uz*uz*(1-cos)
    ]]
    return R1

    # # alternate way
    # u = axisN[np.newaxis]
    # v = np.array([[
    #     0, -uz, uy],[
    #     uz, 0, -ux],[
    #     -uy, ux, 0
    # ]])

    # R2 = cos*np.identity(3) + sin*v + (1-cos)*(u.T@u)
    # return R2

# FIXME: homogenize points FIRST, then vectorize points (w becomes 0!), then do all calculations
# see https://community.khronos.org/t/adding-homogeneous-coordinates-is-too-easy/49573
def getBoxMatrix(trksQApath='../input/TrksQA/box-2.00/'):

    # TODO: read from config or PANDA db/survey
    lumiPos = getLumiPosition()
    print(f'Lumi Position is:\n{lumiPos}')

    #ipApparent = np.array([1.0, 0.0, 0.0])
    ipApparent = getIPfromTrksQA(trksQApath+'Lumi_TrksQA_100000.root')
    # TODO: read from config or PANDA db/survey
    ipActual = np.array([0.0, 0.0, 0.0])

    print(f'IP apparent:\n{ipApparent}')

    ipApparentLMD = ipApparent - lumiPos
    ipActualLMD = ipActual - lumiPos

    #! order is (IP_from_LMD, IP_actual) (i.e. from PANDA)
    Ra = getRot(ipApparentLMD, ipActualLMD)
    R1 = makeHomogenous(Ra)

    print('matrix is:')
    printMatrixDetails(R1)

    # read json file here and compare
    mat1 = getMatrixFromJSON('../input/rootMisalignMatrices/json/misMat-box-2.00.root.json', '/cave_1/lmd_root_0')
    print('matrix should be:')
    printMatrixDetails(mat1)

    printMatrixDetails(R1, mat1)

    resultJson = {"/cave_1/lmd_root_0" : np.ndarray.tolist(np.ndarray.flatten(R1))}
    
    # TODO: generalize the output name, maybe use some command line argument
    with open('../output/alMat-box-2.00.json', 'w') as outfile:  
        json.dump(resultJson, outfile, indent=2)

    print(resultJson)

    ipA1 = makeHomogenous(ipActualLMD).T
    print(f'ip A1: \n{ipA1}')
    print(f'with this matrix, the IP would be at:\n{(np.linalg.inv(R1)@ipA1).T[0] + makeHomogenous(lumiPos)}')

# implement anew using strictly homogenous, column-major vectors!
# points have w=1, vectors have w=0. de-homogenize with x/w, y/w, z/w
# vectors CAN NOT be de-homogenized, but the points they point to can be.
# so, construct a point by origin + v = point_v_points_to
# this way, v gets its w=1 back
# see https://math.stackexchange.com/questions/645672/what-is-the-difference-between-a-point-and-a-vector
def getBoxMatrixHomogenous():
    origin = np.array([0,0,0,1])[np.newaxis].T
    pass

if __name__ == "__main__":
    print('greetings, human.')

    parser = argparse.ArgumentParser()
    parser.add_argument('-p', type=str, dest='path', help='TrksQA_100000.root path')
    args = parser.parse_args()

    print(f'searching in {args.path}')

    if args.path is not None:
        getBoxMatrix(args.path)
    else:
        getBoxMatrix()
    print('all done!')