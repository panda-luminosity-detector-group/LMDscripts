#!/usr/bin/env python3


# limit openblas's max threads, this must be done BEFORE importing numpy
import sys
import random
import json
import datetime
import concurrent
import argparse
from pathlib import Path
from detail.simWrapper import simWrapper
from detail.matrixComparator import *
from detail.LumiValLaTeXTable import LumiValLaTeXTable
from detail.logger import LMDrunLogger
from detail.LMDRunConfig import LMDRunConfig
from concurrent.futures import ThreadPoolExecutor
from alignment.alignerSensors import alignerSensors
from alignment.alignerIP import alignerIP
from alignment.alignerModules import alignerModules
from argparse import RawTextHelpFormatter

import os
os.environ.update(
    OMP_NUM_THREADS='8',
    OPENBLAS_NUM_THREADS='8',
    NUMEXPR_NUM_THREADS='8',
    MKL_NUM_THREADS='8',
)

"""
Author: R. Klasen, roklasen@uni-mainz.de or r.klasen@gsi.de

This script handles all simulation related abstractions.

it:
- browses paths for TrksQA, reco_ip and lumi_vals
- runs simulations (aligned, misaligned)
- runs aligner scripts (alignIP, alignSensors, alignCorridors)
- re-runs simulations with align matrices
- runs ./determineLuminosity
- runs ./extractLuminosity
- compares lumi values before and after alignment
- compares found alignment matrices with actual alignment matrices
- converts JSON to ROOT matrices and vice-versa (with ROOT macros)

---------- steps in ideal geometry sim ----------

- run ./doSimulationReconstruction wit correct parameters (doesn't block)
- run ./determineLuminosity (blocks!)
- run ./extractLuminosity   (blocks!)

---------- steps in misaligned geometry sim ----------

- run ./doSimulationReconstruction wit correct parameters
- run ./determineLuminosity
- run ./extractLuminosity

---------- steps in misaligned geometry with correction ----------
- run ./doSimulationReconstruction with correct parameters
- run ./determineLuminosity
- run ./extractLuminosity
- run my aligners
- rerun ./doSimulationReconstruction with correct parameters
- rerun ./determineLuminosity
- rerun ./extractLuminosity

"""

# to easily copy files, monkey patch
def _copy(self, target):
    import shutil
    try:
        assert self.is_file()
    except:
        print(f'ERROR! {self} is not a file!')
    shutil.copy(self, target) 
Path.copy = _copy

def startLogToFile(functionName=None):

    if functionName is None:
        runSimLog = f'runLogs/{datetime.date.today()}/run{runNumber}-main.log'
        runSimLogErr = f'runLogs/{datetime.date.today()}/run{runNumber}-main-stderr.log'

    else:
        runSimLog = f'runLogs/{datetime.date.today()}/run{runNumber}-main-{functionName}.log'
        runSimLogErr = f'runLogs/{datetime.date.today()}/run{runNumber}-main-{functionName}-stderr.log'

    # redirect stdout/stderr to log files
    print(f'+++ starting new run and forking to background! this script will write all output to {runSimLog}\n')
    Path(runSimLog).parent.mkdir(exist_ok=True)
    sys.stdout = open(runSimLog, 'a+')
    sys.stderr = open(runSimLogErr, 'a+')
    print(f'+++ starting new run at {datetime.datetime.now()}:\n')


def done():
    print(f'\n\n====================================\n')
    print(f'       all tasks completed!           ')
    print(f'\n====================================\n\n')
    # cleanup, probably not neccessary
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    parser.exit(0)


# TODO: remove further code duplication in these functions!

# ? =========== functions that can be run by runAllConfigsMT

def runAligners(runConfig, threadID=None):

    print(f'Thread {threadID}: starting!')

    externalMatPath = Path(runConfig.sensorAlignExternalMatrixPath)

    sensorAlignerOverlapsResultName = runConfig.pathAlMatrixPath() / Path(f'alMat-sensorOverlaps-{runConfig.misalignFactor}.json')
    sensorAlignerResultName = runConfig.pathAlMatrixPath() / Path(f'alMat-sensorAlignment-{runConfig.misalignFactor}.json')
    IPalignerResultName = runConfig.pathAlMatrixPath() / Path(f'alMat-IPalignment-{runConfig.misalignFactor}.json')
    mergedAlignerResultName = runConfig.pathAlMatrixPath() / Path(f'alMat-merged.json')

    # create logger
    thislogger = LMDrunLogger(f'./runLogs/{datetime.date.today()}/run{runNumber}-worker-Alignment-{runConfig.misalignType}-{runConfig.misalignFactor}-th{threadID}.txt')
    thislogger.log(runConfig.dump())

    # create alignerSensors, run
    sensorAligner = alignerSensors.fromRunConfig(runConfig)
    sensorAligner.loadExternalMatrices(externalMatPath)
    sensorAligner.sortPairs()
    sensorAligner.findMatrices()
    sensorAligner.saveOverlapMatrices(sensorAlignerOverlapsResultName)
    sensorAligner.combineAlignmentMatrices()
    sensorAligner.saveAlignmentMatrices(sensorAlignerResultName)

    # create alignerIP, run
    IPaligner = alignerIP.fromRunConfig(runConfig)
    IPaligner.logger = thislogger
    IPaligner.computeAlignmentMatrix()
    IPaligner.saveAlignmentMatrix(IPalignerResultName)

    # create alignerCorridors, run

    # combine all alignment matrices to one single json File
    with open(sensorAlignerResultName, 'r') as f:
        resOne = json.load(f)

    with open(IPalignerResultName, 'r') as f:
        resTwo = json.load(f)

    mergedResult = {}

    for p in resOne:
        mergedResult[p] = resOne[p]

    for p in resTwo:
        mergedResult[p] = resTwo[p]

    with open(mergedAlignerResultName, 'w') as f:
        json.dump(mergedResult, f, indent=2)
    print(f'Wrote merged alignment matrices to {mergedAlignerResultName}')

    print(f'Thread {threadID} done!')


def runComparators():
    pass


def runExtractLumi(runConfig, threadID=None):

    print(f'Thread {threadID}: starting!')

    # create logger
    thislogger = LMDrunLogger(f'./runLogs/{datetime.date.today()}/run{runNumber}-worker-ExtractLumi-{runConfig.misalignType}-{runConfig.misalignFactor}-th{threadID}.txt')
    thislogger.log(runConfig.dump())

    # create simWrapper from config
    prealignWrapper = simWrapper.fromRunConfig(runConfig)
    prealignWrapper.threadNumber = threadID
    prealignWrapper.logger = thislogger

    # run
    prealignWrapper.extractLumi()              # blocking

    print(f'Thread {threadID} done!')


def runLumifit(runConfig, threadID=None):

    print(f'Thread {threadID}: starting!')

    # create logger
    thislogger = LMDrunLogger(f'./runLogs/{datetime.date.today()}/run{runNumber}-worker-LumiFit-{runConfig.misalignType}-{runConfig.misalignFactor}-th{threadID}.txt')
    thislogger.log(runConfig.dump())

    # create simWrapper from config
    prealignWrapper = simWrapper.fromRunConfig(runConfig)
    prealignWrapper.threadNumber = threadID
    prealignWrapper.logger = thislogger

    # run
    prealignWrapper.detLumi()                  # not blocking!
    prealignWrapper.waitForJobCompletion()     # waiting
    prealignWrapper.extractLumi()              # blocking

    print(f'Thread {threadID} done!')


def runSimRecoLumi(runConfig, threadID=None):

    print(f'Thread {threadID}: starting!')

    # create logger
    thislogger = LMDrunLogger(f'./runLogs/{datetime.date.today()}/run{runNumber}-worker-SimReco-{runConfig.misalignType}-{runConfig.misalignFactor}-th{threadID}.txt')
    thislogger.log(runConfig.dump())

    # create simWrapper from config
    prealignWrapper = simWrapper.fromRunConfig(runConfig)
    prealignWrapper.threadNumber = threadID
    prealignWrapper.logger = thislogger

    # run all
    prealignWrapper.runSimulations()           # non blocking, so we have to wait
    prealignWrapper.waitForJobCompletion()     # blocking

    print(f'Thread {threadID} done!')


def runSimRecoLumiAlignRecoLumi(runConfig, threadID=None):

    print(f'Thread {threadID}: starting!')

    # start with a config, not a wrapper
    # add a filter, if the config assumes alignment correction, discard

    if runConfig.alignmentCorrection:
        print(f'Thread {threadID}: this runConfig contains a correction, ignoring')
        print(f'Thread {threadID}: done!')
        return

    # create logger
    thislogger = LMDrunLogger(f'./runLogs/{datetime.date.today()}/run{runNumber}-worker-FullRun-{runConfig.misalignType}-{runConfig.misalignFactor}-th{threadID}.txt')

    # create simWrapper from config
    prealignWrapper = simWrapper.fromRunConfig(runConfig)
    prealignWrapper.threadNumber = threadID
    prealignWrapper.logger = thislogger

    # run all
    prealignWrapper.runSimulations()           # non blocking, so we have to wait
    prealignWrapper.waitForJobCompletion()     # blocking
    prealignWrapper.detLumi()                  # not blocking
    prealignWrapper.waitForJobCompletion()     # waiting
    prealignWrapper.extractLumi()              # blocking

    # then run aligner(s)
    runAligners(runConfig, threadID)

    # then, set align correction in config true and recreate simWrapper
    runConfig.alignmentCorrection = True

    postalignWrapper = simWrapper.fromRunConfig(runConfig)
    prealignWrapper.threadNumber = threadID
    postalignWrapper.logger = thislogger

    # re run reco steps and Lumi fit
    postalignWrapper.runSimulations()           # non blocking, so we have to wait
    postalignWrapper.waitForJobCompletion()     # blocking
    postalignWrapper.detLumi()                  # not blocking
    postalignWrapper.waitForJobCompletion()     # waiting
    postalignWrapper.extractLumi()              # blocking

    print(f'Thread {threadID} done!')


def showLumiFitResults(runConfigPath, threadID=None):

    # read all configs from path
    runConfigPath = Path(runConfigPath)
    configFiles = list(runConfigPath.glob('**/*.json'))

    configs = []
    for file in configFiles:
        config = LMDRunConfig.fromJSON(file)
        configs.append(config)

    if len(configs) == 0:
        print(f'No runConfig files found in {runConfigPath}!')

    table = LumiValLaTeXTable.fromConfigs(configs)
    table.show()


def histogramRunConfig(runConfig, threadId=0):
    # ? comparator starts here


    # box rotation comparator
    comparator = boxComparator()
    comparator.loadIdealDetectorMatrices('input/detectorMatricesIdeal.json')
    comparator.loadDesignMisalignments(runConfig.pathMisMatrix())
    comparator.loadAlignerMatrices(runConfig.pathAlMatrixPath() / Path(f'alMat-merged.json'))
    comparator.saveHistogram(f'output/comparison/{runConfig.momentum}/misalign-{runConfig.misalignType}/box-{runConfig.misalignFactor}-icp.pdf')

    # # overlap comparator
    comparator = overlapComparator()
    comparator.loadIdealDetectorMatrices('input/detectorMatricesIdeal.json')
    comparator.loadDesignMisalignments(runConfig.pathMisMatrix())
    comparator.loadSensorAlignerOverlapMatrices(runConfig.pathAlMatrixPath() / Path(f'alMat-sensorOverlaps-{runConfig.misalignFactor}.json'))
    comparator.loadPerfectDetectorOverlaps('input/detectorOverlapsIdeal.json')
    comparator.saveHistogram(f'output/comparison/{runConfig.momentum}/misalign-{runConfig.misalignType}/sensor-overlaps-{runConfig.misalignFactor}-icp.pdf')

    # combined comparator
    comparator = combinedComparator()
    comparator.loadIdealDetectorMatrices('input/detectorMatricesIdeal.json')
    comparator.loadDesignMisalignments(runConfig.pathMisMatrix())
    comparator.loadAlignerMatrices(runConfig.pathAlMatrixPath() / Path(f'alMat-merged.json'))
    comparator.saveHistogram(f'output/comparison/{runConfig.momentum}/misalign-{runConfig.misalignType}/sensors-{runConfig.misalignFactor}-misalignments.pdf')

# ? =========== runAllConfigsMT that calls 'function' multithreaded


def runConfigsMT(args, function):

    configs = []
    # read all configs from path
    searchDir = Path(args.configPath)

    if args.recursive:
        configs = list(searchDir.glob('**/*.json'))
    else:
        configs = list(searchDir.glob('*.json'))

    if len(configs) == 0:
        print(f'No runConfig files found in {searchDir}. Exiting!')
        sys.exit(1)

    simConfigs = []

    # loop over all configs, create wrapper and run
    for configFile in configs:
        runConfig = LMDRunConfig.fromJSON(configFile)
        simConfigs.append(runConfig)

    threads = 64
    maxThreads = min(len(simConfigs), threads)
    print(f'INFO: running in {maxThreads} threads!')

    if args.debug:
        maxThreads = 1

        for con in simConfigs:
            con.useDebug = True
            function(con, 0)

    else:
        # run concurrently in maximum 64 threads. they mostly wait for compute nodes anyway.
        # we use a process pool instead of a thread pool because the individual interpreters are working on different cwd's.
        # although I don't think that's actually needed...
        with concurrent.futures.ProcessPoolExecutor(max_workers=maxThreads) as executor:
            # Start the load operations and mark each future with its URL
            for index, config in enumerate(simConfigs):
                executor.submit(function, config, index)

        print('waiting for all jobs...')
        executor.shutdown(wait=True)
    return


def runConfigsST(args, function):
    configs = []
    # read all configs from path
    searchDir = Path(args.configPath)

    if args.recursive:
        configs = list(searchDir.glob('**/*.json'))
    else:
        configs = list(searchDir.glob('*.json'))

    if len(configs) == 0:
        print(f'No runConfig files found in {searchDir}. Exiting!')
        sys.exit(1)

    simConfigs = []

    # loop over all configs, create wrapper and run
    for configFile in configs:
        runConfig = LMDRunConfig.fromJSON(configFile)
        simConfigs.append(runConfig)

    for con in simConfigs:
        function(con, 0)

    return


def createMultipleDefaultConfigs():
    # for now
    correctedOptions = [False, True]
    momenta = ['1.5', '15.0']
    # momenta = ['1.5', '4.06', '8.9', '11.91', '15.0']
    misFactors = {}
    misTypes = ['aligned', 'identity', 'sensors', 'box', 'boxRotZ', 'modules', 'singlePlane']
    
    misFactors['aligned'] =     ['1.00']
    misFactors['identity'] =    ['1.00']
    misFactors['sensors'] =     ['0.10', '0.50', '1.00', '2.00', '5.00']
    misFactors['box'] =         ['0.50', '1.00', '2.00']
    misFactors['singlePlane'] = ['0.50', '1.00', '2.00']
    misFactors['boxRotZ'] =     ['1.00', '2.00', '3.00', '5.00', '10.00']
    misFactors['modules'] =     ['0.50', '1.00', '2.00']
    # misFactors['modules'] =     ['0.01', '0.10', '0.15', '0.25', '0.50', '1.00']


    for misType in misTypes:
        for mom in momenta:
            for fac in misFactors[misType]:
                for corr in correctedOptions:

                    if corr:
                        corrPath = 'corrected'
                    else:
                        corrPath = 'uncorrected'

                    dest = Path('runConfigs') / Path(corrPath) / Path(misType) / Path(mom) / Path(f'factor-{fac}.json')
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    config = LMDRunConfig.minimalDefault()

                    config.misalignFactor = fac
                    config.misalignType = misType
                    config.momentum = mom
                    config.alignmentCorrection = corr

                    # identity and aligned don't get factors, only momenta and need fewer pairs
                    if misType == 'aligned' or misType == 'identity':
                        config.useIdentityMisalignment = True
                        config.jobsNum = '100'
                        config.trksNum = '100000'

                    # sensors need more FOR NOW. later, all misalignments are combined and ALL need more
                    # they also need the actual external matrices path
                    if misType == 'sensors':
                        #config.jobsNum = '500'
                        #config.trksNum = '1000000'
                        config.sensorAlignExternalMatrixPath = f'input/sensorAligner/externalMatrices-sensors-{fac}.json'

                    # ? ----- special cases here
                    # aligned case has no misalignment
                    if misType == 'aligned':
                        config.misaligned = False

                    if Path(dest).exists():
                        continue

                    config.toJSON(dest)

    # regenerate missing fields
    targetDir = Path('runConfigs')
    configs = [x for x in targetDir.glob('**/*.json') if x.is_file()]
    for fileName in configs:
        conf = LMDRunConfig.fromJSON(fileName)
        conf.generateMatrixNames()
        conf.toJSON(fileName)


# ? =========== main user interface

if __name__ == "__main__":

    # if os.fork():
    #     syns.exit()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=RawTextHelpFormatter)

    parser.add_argument('-a', metavar='--alignConfig', type=str, dest='alignConfig', help='find all alignment matrices (IP, corridor, sensors) for runConfig')
    parser.add_argument('-A', metavar='--alignConfigPath', type=str, dest='alignConfigPath', help='same as -a, but for all Configs in specified path')

    parser.add_argument('-f', metavar='--fullRunConfig', type=str, dest='fullRunConfig', help='Do a full run (simulate mc data, find alignment, determine Luminosity)')
    parser.add_argument('-F', metavar='--fullRunConfigPath', type=str, dest='fullRunConfigPath', help='same as -f, but for all Configs in specified path')

    parser.add_argument('-l', metavar='--lumifitConfig', type=str, dest='lumifitConfig', help='determine Luminosity for runConfig')
    parser.add_argument('-L', metavar='--lumifitConfigPath', type=str, dest='lumifitConfigPath', help='same as -l, but for all Configs in specified path')

    parser.add_argument('-el', metavar='--extractLumiConfig', type=str, dest='extractLumiConfig', help='extract Luminosity for runConfig')
    parser.add_argument('-EL', metavar='--extractLumiConfigPath', type=str, dest='extractLumiConfigPath', help='same as -el, but for all Configs in specified path')

    parser.add_argument('-s', metavar='--simulationConfig', type=str, dest='simulationConfig', help='run simulation and reconstruction for runConfig')
    parser.add_argument('-S', metavar='--simulationConfigPath', type=str, dest='simulationConfigPath', help='same as -s, but for all Configs in specified path')

    #parser.add_argument('-v', metavar='--fitValuesConfig', type=str, dest='fitValuesConfig', help='display reco_ip and lumi_vals for select runConfig (if found)')
    parser.add_argument('-V', metavar='--fitValuesConfigPath', type=str, dest='fitValuesConfigPath', help='display reco_ip and lumi_vals for all runConfigs in path')

    parser.add_argument('-r', action='store_true', dest='recursive', help='use with any config Path option to scan paths recursively')

    parser.add_argument('--hist', type=str, dest='histSensorAligner', help='hist matrix deviations for runConfig')
    parser.add_argument('--histPath', type=str, dest='histSensorAlignerPath', help='hist matrix deviations for all runConfigs in Path, recursively')

    parser.add_argument('-d', action='store_true', dest='makeDefault', help='make a single default LMDRunConfig and save it to runConfigs/identity-1.00.json')
    parser.add_argument('-D', action='store_true', dest='makeMultipleDefaults', help='make multiple example LMDRunConfigs')
    parser.add_argument('--debug', action='store_true', dest='debug', help='run single threaded, more verbose output, submit jobs to devel queue')
    parser.add_argument('--updateRunConfigs', dest='updateRunConfigs', help='read all configs in ./runConfig, recreate the matrix file paths and store them.')
    parser.add_argument('--test', action='store_true', dest='test', help='internal test function')

    try:
        args = parser.parse_args()
    except:
        # parser.print_help()
        parser.exit(1)

    if len(sys.argv) < 2:
        parser.print_help()
        parser.exit(1)

    # random number to identify runs
    global runNumber
    runNumber = random.randint(0, 100)

    # ? =========== helper functions
    if args.makeDefault:
        dest = Path('runConfigs/identity-1.00.json')
        print(f'saving default config to {dest}')
        LMDRunConfig.minimalDefault().toJSON(dest)
        done()

    if args.makeMultipleDefaults:
        createMultipleDefaultConfigs()
        done()

    if args.updateRunConfigs:

        targetDir = Path(args.updateRunConfigs).absolute()
        print(f'reading all files from {targetDir} and regenerating settings...')

        configs = [x for x in targetDir.glob('**/*.json') if x.is_file()]

        for fileName in configs:
            conf = LMDRunConfig.fromJSON(fileName)
            conf.updateEnvPaths()
            conf.generateMatrixNames()
            conf.toJSON(fileName)
        done()

    if args.test:
        print(f'Testing...')
        alignerMod = alignerModules()
        # alignerMod.alignICPold()
        # alignerMod.alignMillepede()
        alignerMod.alignICPiterative()

        done()

        comp = moduleComparator()
        comp.loadIdealDetectorMatrices('input/detectorMatricesIdeal.json')
        
        comp.loadDesignMisalignments('input/misMat-identity-1.00.json')
        # comp.loadDesignMisalignments(f'input/allDetectorMatrices-singlePlane-2.00.json')
        # comp.loadDesignMisalignments('input/misMat-modules-1.00.json')

        comp.loadAlignerMatrices('output/alignmentModules/alMat-modules-1.0.json')
        # comp.loadAlignerMatrices('output/alignmentModules/alMat-aligned.json')
        # comp.loadAlignerMatrices('output/alignmentModules/alMat-modules-singlePlane.json')
        comp.saveHistogram('output/alignmentModules/lawl.pdf')

        done()

    if args.debug:
        print(f'\n\n!!! Running in debug mode !!!\n\n')

    # ? =========== histogram Aligner results
    if args.histSensorAligner:
        runConfig = LMDRunConfig.fromJSON(args.histSensorAligner)

        if args.debug:
            runConfig.useDebug = True
        histogramRunConfig(runConfig)
        done()

    if args.histSensorAlignerPath:
        args.configPath = args.histSensorAlignerPath
        runConfigsST(args, histogramRunConfig)
        done()

    # ? =========== lumi fit results, single config
    # if args.fitValuesConfig:
    #     config = LMDRunConfig.fromJSON(args.fitValuesConfig)
    #     if args.debug:
    #         config.useDebug = True
    #     showLumiFitResults(config, 99)
    #     done()

    # ? =========== lumi fit results, multiple configs
    if args.fitValuesConfigPath:
        #args.configPath = args.fitValuesConfigPath
        showLumiFitResults(args.fitValuesConfigPath)
        done()

    #! ---------------------- logging goes to file if not in debug mode

    if not args.debug:
        if os.fork():
            sys.exit()

    # ? =========== align, single config
    if args.alignConfig:
        startLogToFile('Align')
        config = LMDRunConfig.fromJSON(args.alignConfig)
        if args.debug:
            config.useDebug = True
        runAligners(config, 99)
        done()

    # ? =========== align, multiple configs
    if args.alignConfigPath:
        startLogToFile('AlignMulti')
        args.configPath = args.alignConfigPath
        runConfigsMT(args, runAligners)
        done()

    # ? =========== lumiFit, single config
    if args.lumifitConfig:
        startLogToFile('LumiFit')
        config = LMDRunConfig.fromJSON(args.lumifitConfig)
        if args.debug:
            config.useDebug = True
        runLumifit(config, 99)
        done()

    # ? =========== lumiFit, multiple configs
    if args.lumifitConfigPath:
        startLogToFile('LumiFitMulti')
        args.configPath = args.lumifitConfigPath
        runConfigsMT(args, runLumifit)
        done()

    # ? =========== extract Lumi, single config
    if args.extractLumiConfig:
        startLogToFile('ExtractLumi')
        config = LMDRunConfig.fromJSON(args.extractLumiConfig)
        if args.debug:
            config.useDebug = True
        runExtractLumi(config, 99)
        done()

    # ? =========== extract Lumi, multiple configs
    if args.extractLumiConfigPath:
        startLogToFile('ExtractLumi')
        args.configPath = args.extractLumiConfigPath
        runConfigsMT(args, runExtractLumi)
        done()

    # ? =========== simReco, single config
    if args.simulationConfig:
        startLogToFile('SimReco')
        config = LMDRunConfig.fromJSON(args.simulationConfig)
        if args.debug:
            config.useDebug = True
        runSimRecoLumi(config, 99)
        done()

    # ? =========== simReco, multiple configs
    if args.simulationConfigPath:
        startLogToFile('SimRecoMulti')
        args.configPath = args.simulationConfigPath
        runConfigsMT(args, runSimRecoLumi)
        done()

    # ? =========== full job, single config
    if args.fullRunConfig:
        startLogToFile('FullRun')
        config = LMDRunConfig.fromJSON(args.fullRunConfig)
        if args.debug:
            config.useDebug = True
        runSimRecoLumiAlignRecoLumi(config, 99)
        done()

    # ? =========== full job, multiple configs
    if args.fullRunConfigPath:
        startLogToFile('FullRunMulti')
        args.configPath = args.fullRunConfigPath
        runConfigsMT(args, runSimRecoLumiAlignRecoLumi)
        done()
