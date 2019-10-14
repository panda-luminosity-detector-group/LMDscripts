#!usr/bin/env python3

from collections import defaultdict  # to concatenate dictionaries
from pathlib import Path
import numpy as np
from numpy.linalg import inv
import json
import uproot
import re
import sys

"""

Author: R. Klasen, roklasen@uni-mainz.de or r.klasen@gsi.de

This file handles reading of lmd_tracks and lmd reco_files, and extracts tracks with their corresponding reco hist.

It then gives those values to millepede, obtains the alignment parameters back and writes them to alignment matrices.
"""

class trackReader():
    def __init__(self):
        self.trks = {}
        pass

    """
    sector goes from 0 to 9
    """
    def readTracksFromRoot(self, path, sector=-1):
        """
        Currently not working, please use the json method
        """
        pass

    def readTracksFromJson(self, filename, sector=-1):
        with open(filename, 'r') as infile:
            self.trks = json.load(infile)['events']
            print('file successfully read!')

    def readDetectorParameters(self):
        with open(Path('input/detectorOverlapsIdeal.json')) as inFile:
            self.detectorOverlaps = json.load(inFile)

        with open(Path('input/detectorMatricesIdeal.json')) as inFile:
            self.detectorMatrices = json.load(inFile)

    def getPathModuleFromSensorID(self, sensorID):
        for overlap in self.detectorOverlaps:
            if self.detectorOverlaps[overlap]['id1'] == sensorID or self.detectorOverlaps[overlap]['id2'] == sensorID:
                return self.detectorOverlaps[overlap]["pathModule"]

    def getParamsFromModulePath(self, modulePath):
        # "/cave_1/lmd_root_0/half_1/plane_0/module_3"
        regex = r"/cave_(\d+)/lmd_root_(\d+)/half_(\d+)/plane_(\d+)/module_(\d+)"
        p = re.compile(regex)
        m = p.match(modulePath)
        if m:
            half = int(m.group(3))
            plane = int(m.group(4))
            module = int(m.group(5))
            sector = (module) + (half)*5;
            return half, plane, module, sector
    
    def generatorMilleParameters(self):

        print(f'no of events: {len(self.trks)}')

        # TODO: use vectorized version to use numpy!

        # loop over all events
        for event in self.trks:

            # track origin and direction
            trackOri = np.array(event['trkPos'])
            trackDir = np.array(event['trkMom']) / np.linalg.norm(event['trkMom'])

            for reco in event['recoHits']:
                # print(f'hit index: {reco["index"]}')
                # print(f"reco hit pos: {reco['pos']}")
                # print(f"reco hit err: {reco['err']}")
                recoPos = np.array(reco['pos'])

                sensorID = reco['sensorID']
                modulePath = self.getPathModuleFromSensorID(sensorID)

                # determine module position from reco hit
                half, plane, module, sector = self.getParamsFromModulePath(modulePath)

                # create path to first module in this sector
                pathFirstMod = f"/cave_1/lmd_root_0/half_{half}/plane_0/module_{module}"

                # get matrix to first module
                matrixFirstMod = np.array(self.detectorMatrices[pathFirstMod]).reshape(4,4)

                # homogenize reco hit
                recoH = np.ones(4)
                recoH[:3] = recoPos
                recoH = recoH.reshape((1,4)).T

                # transform track origin, direction and reco hit position (vectors must be col-major amd homogenous!)
                trackOriH = np.ones(4)
                trackOriH[:3] = trackOri
                
                trackDirH = np.ones(4)
                trackDirH[:3] = trackDir

                trackOriH = trackOriH.reshape((1,4)).T
                trackDirH = trackDirH.reshape((1,4)).T

                # transform
                trackOriH = inv(matrixFirstMod) @ trackOriH
                trackDirH = inv(matrixFirstMod) @ trackDirH
                recoH = inv(matrixFirstMod) @ recoH         # warning, not a unit vector anymore!

                # de-homogenize
                trackOriNew = trackOriH[:3] / trackOriH[3]
                trackDirNew = trackDirH[:3] / trackDirH[3]
                # make direction unit length
                trackDirNew = trackDirNew / np.linalg.norm(trackDirNew)
                recoNew = recoH[:3] / recoH[3]

                # and re-transpose
                trackOriNew = trackOriNew.T.reshape(3)
                trackDirNew = trackDirNew.T.reshape(3)
                recoNew = recoNew.T.reshape(3)

                # print(f'\ntrackOri: {trackOri}\ntrackDir: {trackDir}\n')
                # print(f'\ntrackOriH: {trackOriH}\ntrackDirH: {trackDirH}\n')
                # print(f'\nreco: {reco["pos"]}\nrecoH: {recoH}\n')

                # print(f'de-homogenized:')
                # print(trackOriNew, "\n", trackDirNew, "\n", recoNew)

                # better way calculate vector from reco point to track
                # https://en.wikipedia.org/wiki/Distance_from_a_point_to_a_line
                dVec = (trackOriNew - recoNew) - ((trackOriNew - recoNew)@trackDirNew) * trackDirNew

                px = recoNew[0]
                py = recoNew[1]
                dz = recoNew[2]

                # print(f'------------------------')
                
                # print(f'dVec: [dVec]')
                # print(f'-this dist: [np.linalg.norm(dVec)]')
                # print(f'other dist: [dist]')

                # okay, at this point, I have all positions, distances and errors in x and y

                if plane == 0:
                    yield from self.milleParamsPlaneOne(px, py, dz, dVec, reco['err'], sector)
                
                elif plane == 1:
                    yield from self.milleParamsPlaneTwo(px, py, dz, dVec, reco['err'], sector)
                
                elif plane == 2:
                    yield from self.milleParamsPlaneThree(px, py, dz, dVec, reco['err'], sector)
                
                elif plane == 3:
                    yield from self.milleParamsPlaneFour(px, py, dz, dVec, reco['err'], sector)
            
    
    """
    yield TWO lines, one for x, one for y
    """
    def milleParamsPlaneOne(self, px, py, dz, dVec, errVec, sector):
        #! ============== first plane
        #* -------------- x values
        #? ------ global derivtives
        derGL = [ -1, 0, -py,       0,0,0,      0,0,0,      0,0,0]
        #? ------ local derivtives
        derLC = [ 1, dz, 0, 0]
        #? ------ residual, error
        residual = dVec[0]   # distance x reco to track!
        sigma = errVec[0]    # error in x direction         
        
        yield (derGL, derLC, residual, sigma, sector)

        #* -------------- y values
        #? ------ global derivtives
        derGL = [ 0, -1, px,       0,0,0,      0,0,0,       0,0,0]
        #? ------ local derivtives
        derLC = [ 0, 0, 1, dz]
        #? ------ residual, error
        residual = dVec[1]   # distance y reco to track!
        sigma = errVec[1]    # error in y direction   

        yield (derGL, derLC, residual, sigma, sector)

    def milleParamsPlaneTwo(self, px, py, dz, dVec, errVec, sector):
        #! ============== second plane
        #* -------------- x values
        #? ------ global derivtives
        derGL = [0,0,0,     -1, 0, -py,     0,0,0,      0,0,0]
        #? ------ local derivtives
        derLC = [ 1, dz, 0, 0]
        #? ------ residual, error
        residual = dVec[0]   # distance x reco to track!
        sigma = errVec[0]    # error in x direction         
        
        yield (derGL, derLC, residual, sigma, sector)

        #* -------------- y values
        #? ------ global derivtives
        derGL = [0,0,0,     0, -1, px,      0,0,0,      0,0,0]
        #? ------ local derivtives
        derLC = [ 0, 0, 1, dz]
        #? ------ residual, error
        residual = dVec[1]   # distance y reco to track!
        sigma = errVec[1]    # error in y direction   

        yield (derGL, derLC, residual, sigma, sector)

    def milleParamsPlaneThree(self, px, py, dz, dVec, errVec, sector):
        #! ============== third plane
        #* -------------- x values
        #? ------ global derivtives
        derGL = [0,0,0,     0,0,0,      -1, 0, -py,     0,0,0]
        #? ------ local derivtives
        derLC = [ 1, dz, 0, 0]
        #? ------ residual, error
        residual = dVec[0]   # distance x reco to track!
        sigma = errVec[0]    # error in x direction         
        
        yield (derGL, derLC, residual, sigma, sector)

        #* -------------- y values
        #? ------ global derivtives
        derGL = [0,0,0,     0,0,0,      0, -1, px,      0,0,0]
        #? ------ local derivtives
        derLC = [ 0, 0, 1, dz]
        #? ------ residual, error
        residual = dVec[1]   # distance y reco to track!
        sigma = errVec[1]    # error in y direction   

        yield (derGL, derLC, residual, sigma, sector)

    def milleParamsPlaneFour(self, px, py, dz, dVec, errVec, sector):
        #! ============== third plane
        #* -------------- x values
        #? ------ global derivtives
        derGL = [0,0,0,     0,0,0,      0,0,0,      -1, 0, -py]
        #? ------ local derivtives
        derLC = [ 1, dz, 0, 0]
        #? ------ residual, error
        residual = dVec[0]   # distance x reco to track!
        sigma = errVec[0]    # error in x direction         
        
        yield (derGL, derLC, residual, sigma, sector)

        #* -------------- y values
        #? ------ global derivtives
        derGL = [0,0,0,     0,0,0,      0,0,0,      0, -1, px]
        #? ------ local derivtives
        derLC = [ 0, 0, 1, dz]
        #? ------ residual, error
        residual = dVec[1]   # distance y reco to track!
        sigma = errVec[1]    # error in y direction   

        yield (derGL, derLC, residual, sigma, sector)













        
        