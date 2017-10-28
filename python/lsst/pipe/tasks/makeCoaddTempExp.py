#
# LSST Data Management System
# Copyright 2008, 2009, 2010, 2011, 2012 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

from __future__ import absolute_import, division, print_function
import numpy

import lsst.pex.config as pexConfig
import lsst.afw.image as afwImage
import lsst.coadd.utils as coaddUtils
import lsst.pipe.base as pipeBase
import lsst.log as log
from lsst.meas.algorithms import CoaddPsf, CoaddPsfConfig
from .coaddBase import CoaddBaseTask
from .warpAndPsfMatch import WarpAndPsfMatchTask
from .coaddHelpers import groupPatchExposures, getGroupDataRef

__all__ = ["MakeCoaddTempExpTask"]


class MakeCoaddTempExpConfig(CoaddBaseTask.ConfigClass):
    """Config for MakeCoaddTempExpTask
    """
    warpAndPsfMatch = pexConfig.ConfigurableField(
        target=WarpAndPsfMatchTask,
        doc="Task to warp and PSF-match calexp",
    )
    doWrite = pexConfig.Field(
        doc="persist <coaddName>Coadd_<warpType>Warp",
        dtype=bool,
        default=True,
    )
    doOverwrite = pexConfig.Field(
        doc="overwrite <coaddName>Coadd_<warpType>Warp; If False, continue if the file exists on disk",
        dtype=bool,
        default=True,
    )
    bgSubtracted = pexConfig.Field(
        doc="Work with a background subtracted calexp?",
        dtype=bool,
        default=True,
    )
    coaddPsf = pexConfig.ConfigField(
        doc="Configuration for CoaddPsf",
        dtype=CoaddPsfConfig,
    )
    makeDirect = pexConfig.Field(
        doc="Make direct Warp/Coadds",
        dtype=bool,
        default=True,
    )
    makePsfMatched = pexConfig.Field(
        doc="Make Psf-Matched Warp/Coadd?",
        dtype=bool,
        default=False,
    )

    def validate(self):
        CoaddBaseTask.ConfigClass.validate(self)
        if not self.makePsfMatched and not self.makeDirect:
            raise RuntimeError("At least one of config.makePsfMatched and config.makeDirect must be True")
        if self.doPsfMatch:
            # Backwards compatibility.
            log.warn("Config doPsfMatch deprecated. Setting makePsfMatched=True and makeDirect=False")
            self.makePsfMatched = True
            self.makeDirect = False


## \addtogroup LSST_task_documentation
## \{
## \page MakeCoaddTempExpTask
## \ref MakeCoaddTempExpTask_ "MakeCoaddTempExpTask"
## \copybrief MakeCoaddTempExpTask
## \}


class MakeCoaddTempExpTask(CoaddBaseTask):
    """!Warp and optionally PSF-Match calexps onto an a common projection.

    @anchor MakeCoaddTempExpTask_

    @section pipe_tasks_makeCoaddTempExp_Contents  Contents

     - @ref pipe_tasks_makeCoaddTempExp_Purpose
     - @ref pipe_tasks_makeCoaddTempExp_Initialize
     - @ref pipe_tasks_makeCoaddTempExp_IO
     - @ref pipe_tasks_makeCoaddTempExp_Config
     - @ref pipe_tasks_makeCoaddTempExp_Debug
     - @ref pipe_tasks_makeCoaddTempExp_Example

    @section pipe_tasks_makeCoaddTempExp_Purpose  Description

    Warp and optionally PSF-Match calexps onto a common projection, by
    performing the following operations:
    - Group calexps by visit/run
    - For each visit, generate a Warp by calling method @ref makeTempExp.
      makeTempExp loops over the visit's calexps calling @ref WarpAndPsfMatch
      on each visit

    The result is a `directWarp` (and/or optionally a `psfMatchedWarp`).

    @section pipe_tasks_makeCoaddTempExp_Initialize  Task Initialization

    @copydoc \_\_init\_\_

    This task has no special keyword arguments.

    @section pipe_tasks_makeCoaddTempExp_IO  Invoking the Task

    This task is primarily designed to be run from the command line.

    The main method is `run`, which takes a single butler data reference for the patch(es)
    to process.

    @copydoc run

    WarpType identifies the types of convolutions applied to Warps (previously CoaddTempExps).
    Only two types are available: direct (for regular Warps/Coadds) and psfMatched
    (for Warps/Coadds with homogenized PSFs). We expect to add a third type, likelihood,
    for generating likelihood Coadds with Warps that have been correlated with their own PSF.

    @section pipe_tasks_makeCoaddTempExp_Config  Configuration parameters

    See @ref MakeCoaddTempExpConfig and parameters inherited from
    @link lsst.pipe.tasks.coaddBase.CoaddBaseConfig CoaddBaseConfig @endlink

    @subsection pipe_tasks_MakeCoaddTempExp_psfMatching Guide to PSF-Matching Configs

    To make `psfMatchedWarps`, select `config.makePsfMatched=True`. The subtask
    @link lsst.ip.diffim.modelPsfMatch.ModelPsfMatchTask ModelPsfMatchTask @endlink
    is responsible for the PSF-Matching, and its config is accessed via `config.warpAndPsfMatch.psfMatch`.
    The optimal configuration depends on aspects of dataset: the pixel scale, average PSF FWHM and
    dimensions of the PSF kernel. These configs include the requested model PSF, the matching kernel size,
    padding of the science PSF thumbnail and spatial sampling frequency of the PSF.

    *Config Guidelines*: The user must specify the size of the model PSF to which to match by setting
    `config.modelPsf.defaultFwhm` in units of pixels. The appropriate values depends on science case.
    In general, for a set of input images, this config should equal the FWHM of the visit
    with the worst seeing. The smallest it should be set to is the median FWHM. The defaults
    of the other config options offer a reasonable starting point.
    The following list presents the most common problems that arise from a misconfigured
    @link lsst.ip.diffim.modelPsfMatch.ModelPsfMatchTask ModelPsfMatchTask @endlink
    and corresponding solutions. All assume the default Alard-Lupton kernel, with configs accessed via
    ```config.warpAndPsfMatch.psfMatch.kernel['AL']```. Each item in the list is formatted as:
    Problem: Explanation. *Solution*

    *Troublshooting PSF-Matching Configuration:*
    - Matched PSFs look boxy: The matching kernel is too small. _Increase the matching kernel size.
        For example:_

            config.warpAndPsfMatch.psfMatch.kernel['AL'].kernelSize=27  # default 21

        Note that increasing the kernel size also increases runtime.
    - Matched PSFs look ugly (dipoles, quadropoles, donuts): unable to find good solution
        for matching kernel. _Provide the matcher with more data by either increasing
        the spatial sampling by decreasing the spatial cell size,_

            config.warpAndPsfMatch.psfMatch.kernel['AL'].sizeCellX = 64  # default 128
            config.warpAndPsfMatch.psfMatch.kernel['AL'].sizeCellY = 64  # default 128

        _or increasing the padding around the Science PSF, for example:_

            config.warpAndPsfMatch.psfMatch.autoPadPsfTo=1.6  # default 1.4

        Increasing `autoPadPsfTo` increases the minimum ratio of input PSF dimensions to the
        matching kernel dimensions, thus increasing the number of pixels available to fit
        after convolving the PSF with the matching kernel.
        Optionally, for debugging the effects of padding, the level of padding may be manually
        controlled by setting turning off the automatic padding and setting the number
        of pixels by which to pad the PSF:

            config.warpAndPsfMatch.psfMatch.doAutoPadPsf = False  # default True
            config.warpAndPsfMatch.psfMatch.padPsfBy = 6  # pixels. default 0

    - Deconvolution: Matching a large PSF to a smaller PSF produces
        a telltale noise pattern which looks like ripples or a brain.
        _Increase the size of the requested model PSF. For example:_

            config.modelPsf.defaultFwhm = 11  # Gaussian sigma in units of pixels.

    - High frequency (sometimes checkered) noise: The matching basis functions are too small.
        _Increase the width of the Gaussian basis functions. For example:_

            config.warpAndPsfMatch.psfMatch.kernel['AL'].alardSigGauss=[1.5, 3.0, 6.0]
            # from default [0.7, 1.5, 3.0]


    @section pipe_tasks_makeCoaddTempExp_Debug  Debug variables

    MakeCoaddTempExpTask has no debug output, but its subtasks do.

    @section pipe_tasks_makeCoaddTempExp_Example   A complete example of using MakeCoaddTempExpTask

    This example uses the package ci_hsc to show how MakeCoaddTempExp fits
    into the larger Data Release Processing.
    Set up by running:

        setup ci_hsc
        cd $CI_HSC_DIR
        # if not built already:
        python $(which scons)  # this will take a while

    The following assumes that `processCcd.py` and `makeSkyMap.py` have previously been run
    (e.g. by building `ci_hsc` above) to generate a repository of calexps and an
    output respository with the desired SkyMap. The command,

        makeCoaddTempExp.py $CI_HSC_DIR/DATA --rerun ci_hsc \
         --id patch=5,4 tract=0 filter=HSC-I \
         --selectId visit=903988 ccd=16 --selectId visit=903988 ccd=17 \
         --selectId visit=903988 ccd=23 --selectId visit=903988 ccd=24 \
         --config doApplyUberCal=False makePsfMatched=True modelPsf.defaultFwhm=11

    writes a direct and PSF-Matched Warp to
    - `$CI_HSC_DIR/DATA/rerun/ci_hsc/deepCoadd/HSC-I/0/5,4/warp-HSC-I-0-5,4-903988.fits` and
    - `$CI_HSC_DIR/DATA/rerun/ci_hsc/deepCoadd/HSC-I/0/5,4/psfMatchedWarp-HSC-I-0-5,4-903988.fits`
        respectively.

    @note PSF-Matching in this particular dataset would benefit from adding
    `--configfile ./matchingConfig.py` to
    the command line arguments where `matchingConfig.py` is defined by:

        echo "
        config.warpAndPsfMatch.psfMatch.kernel['AL'].kernelSize=27
        config.warpAndPsfMatch.psfMatch.kernel['AL'].alardSigGauss=[1.5, 3.0, 6.0]" > matchingConfig.py


    Add the option `--help` to see more options.
    """
    ConfigClass = MakeCoaddTempExpConfig
    _DefaultName = "makeCoaddTempExp"

    def __init__(self, **kwargs):
        CoaddBaseTask.__init__(self, **kwargs)
        self.makeSubtask("warpAndPsfMatch")

    @pipeBase.timeMethod
    def run(self, patchRef, selectDataList=[]):
        """!Produce <coaddName>Coadd_<warpType>Warp images by warping and optionally PSF-matching.

        @param[in] patchRef: data reference for sky map patch. Must include keys "tract", "patch",
            plus the camera-specific filter key (e.g. "filter" or "band")
        @return: dataRefList: a list of data references for the new <coaddName>Coadd_directWarps
            if direct or both warp types are requested and <coaddName>Coadd_psfMatchedWarps if only psfMatched
            warps are requested.

        @warning: this task assumes that all exposures in a warp (coaddTempExp) have the same filter.

        @warning: this task sets the Calib of the coaddTempExp to the Calib of the first calexp
        with any good pixels in the patch. For a mosaic camera the resulting Calib should be ignored
        (assembleCoadd should determine zeropoint scaling without referring to it).
        """
        skyInfo = self.getSkyInfo(patchRef)

        # DataRefs to return are of type *_directWarp unless only *_psfMatchedWarp requested
        if self.config.makePsfMatched and not self.config.makeDirect:
            primaryWarpDataset = self.getTempExpDatasetName("psfMatched")
        else:
            primaryWarpDataset = self.getTempExpDatasetName("direct")

        calExpRefList = self.selectExposures(patchRef, skyInfo, selectDataList=selectDataList)
        if len(calExpRefList) == 0:
            self.log.warn("No exposures to coadd for patch %s", patchRef.dataId)
            return None
        self.log.info("Selected %d calexps for patch %s", len(calExpRefList), patchRef.dataId)
        calExpRefList = [calExpRef for calExpRef in calExpRefList if calExpRef.datasetExists("calexp")]
        self.log.info("Processing %d existing calexps for patch %s", len(calExpRefList), patchRef.dataId)

        groupData = groupPatchExposures(patchRef, calExpRefList, self.getCoaddDatasetName(),
                                        primaryWarpDataset)
        self.log.info("Processing %d warp exposures for patch %s", len(groupData.groups), patchRef.dataId)

        dataRefList = []
        for i, (tempExpTuple, calexpRefList) in enumerate(groupData.groups.items()):
            tempExpRef = getGroupDataRef(patchRef.getButler(), primaryWarpDataset,
                                         tempExpTuple, groupData.keys)
            if not self.config.doOverwrite and tempExpRef.datasetExists(datasetType=primaryWarpDataset):
                self.log.info("Warp %s exists; skipping", tempExpRef.dataId)
                dataRefList.append(tempExpRef)
                continue
            self.log.info("Processing Warp %d/%d: id=%s", i, len(groupData.groups), tempExpRef.dataId)

            # TODO: mappers should define a way to go from the "grouping keys" to a numeric ID (#2776).
            # For now, we try to get a long integer "visit" key, and if we can't, we just use the index
            # of the visit in the list.
            try:
                visitId = int(tempExpRef.dataId["visit"])
            except (KeyError, ValueError):
                visitId = i

            exps = self.createTempExp(calexpRefList, skyInfo, visitId).exposures

            if any(exps.values()):
                dataRefList.append(tempExpRef)
            else:
                self.log.warn("Warp %s could not be created", tempExpRef.dataId)

            if self.config.doWrite:
                for (warpType, exposure) in exps.items():  # compatible w/ Py3
                    if exposure is not None:
                        self.log.info("Persisting %s" % self.getTempExpDatasetName(warpType))
                        tempExpRef.put(exposure, self.getTempExpDatasetName(warpType))

        return dataRefList

    def createTempExp(self, calexpRefList, skyInfo, visitId=0):
        """Create a Warp from inputs

        We iterate over the multiple calexps in a single exposure to construct
        the warp (previously called a coaddTempExp) of that exposure to the
        supplied tract/patch.

        Pixels that receive no pixels are set to NAN; this is not correct
        (violates LSST algorithms group policy), but will be fixed up by
        interpolating after the coaddition.

        @param calexpRefList: List of data references for calexps that (may)
            overlap the patch of interest
        @param skyInfo: Struct from CoaddBaseTask.getSkyInfo() with geometric
            information about the patch
        @param visitId: integer identifier for visit, for the table that will
            produce the CoaddPsf
        @return a pipeBase Struct containing:
          - exposures: a dictionary containing the warps requested:
                "direct": direct warp if config.makeDirect
                "psfMatched": PSF-matched warp if config.makePsfMatched
        """
        warpTypeList = self.getWarpTypeList()

        totGoodPix = {warpType: 0 for warpType in warpTypeList}
        didSetMetadata = {warpType: False for warpType in warpTypeList}
        coaddTempExps = {warpType: self._prepareEmptyExposure(skyInfo) for warpType in warpTypeList}
        inputRecorder = {warpType: self.inputRecorder.makeCoaddTempExpRecorder(visitId, len(calexpRefList))
                         for warpType in warpTypeList}

        modelPsf = self.config.modelPsf.apply() if self.config.makePsfMatched else None
        for calExpInd, calExpRef in enumerate(calexpRefList):
            self.log.info("Processing calexp %d of %d for this Warp: id=%s",
                          calExpInd+1, len(calexpRefList), calExpRef.dataId)
            try:
                ccdId = calExpRef.get("ccdExposureId", immediate=True)
            except Exception:
                ccdId = calExpInd
            try:
                # We augment the dataRef here with the tract, which is harmless for loading things
                # like calexps that don't need the tract, and necessary for meas_mosaic outputs,
                # which do.
                calExpRef = calExpRef.butlerSubset.butler.dataRef("calexp", dataId=calExpRef.dataId,
                                                                  tract=skyInfo.tractInfo.getId())
                calExp = self.getCalExp(calExpRef, bgSubtracted=self.config.bgSubtracted)
            except Exception as e:
                self.log.warn("Calexp %s not found; skipping it: %s", calExpRef.dataId, e)
                continue
            try:
                warpedAndMatched = self.warpAndPsfMatch.run(calExp, modelPsf=modelPsf,
                                                            wcs=skyInfo.wcs, maxBBox=skyInfo.bbox,
                                                            makeDirect=self.config.makeDirect,
                                                            makePsfMatched=self.config.makePsfMatched)
            except Exception as e:
                self.log.warn("WarpAndPsfMatch failed for calexp %s; skipping it: %s", calExpRef.dataId, e)
                continue
            try:
                numGoodPix = {warpType: 0 for warpType in warpTypeList}
                for warpType in warpTypeList:
                    exposure = warpedAndMatched.getDict()[warpType]
                    if exposure is None:
                        continue
                    coaddTempExp = coaddTempExps[warpType]
                    if didSetMetadata[warpType]:
                        mimg = exposure.getMaskedImage()
                        mimg *= (coaddTempExp.getCalib().getFluxMag0()[0] /
                                 exposure.getCalib().getFluxMag0()[0])
                        del mimg
                    numGoodPix[warpType] = coaddUtils.copyGoodPixels(
                        coaddTempExp.getMaskedImage(), exposure.getMaskedImage(), self.getBadPixelMask())
                    totGoodPix[warpType] += numGoodPix[warpType]
                    self.log.debug("Calexp %s has %d good pixels in this patch (%.1f%%) for %s",
                                   calExpRef.dataId, numGoodPix[warpType],
                                   100.0*numGoodPix[warpType]/skyInfo.bbox.getArea(), warpType)
                    if numGoodPix[warpType] > 0 and not didSetMetadata[warpType]:
                        coaddTempExp.setCalib(exposure.getCalib())
                        coaddTempExp.setFilter(exposure.getFilter())
                        # PSF replaced with CoaddPsf after loop if and only if creating direct warp
                        coaddTempExp.setPsf(exposure.getPsf())
                        didSetMetadata[warpType] = True

                    # Need inputRecorder for CoaddApCorrMap for both direct and PSF-matched
                    inputRecorder[warpType].addCalExp(calExp, ccdId, numGoodPix[warpType])

            except Exception as e:
                self.log.warn("Error processing calexp %s; skipping it: %s", calExpRef.dataId, e)
                continue

        for warpType in warpTypeList:
            self.log.info("%sWarp has %d good pixels (%.1f%%)",
                          warpType, totGoodPix[warpType], 100.0*totGoodPix[warpType]/skyInfo.bbox.getArea())

            if totGoodPix[warpType] > 0 and didSetMetadata[warpType]:
                inputRecorder[warpType].finish(coaddTempExps[warpType], totGoodPix[warpType])
                if warpType == "direct":
                    coaddTempExps[warpType].setPsf(
                        CoaddPsf(inputRecorder[warpType].coaddInputs.ccds, skyInfo.wcs,
                                 self.config.coaddPsf.makeControl()))
            else:
                # No good pixels. Exposure still empty
                coaddTempExps[warpType] = None

        result = pipeBase.Struct(exposures=coaddTempExps)
        return result

    def _prepareEmptyExposure(cls, skyInfo):
        """Produce an empty exposure for a given patch"""
        exp = afwImage.ExposureF(skyInfo.bbox, skyInfo.wcs)
        exp.getMaskedImage().set(numpy.nan, afwImage.Mask\
                                 .getPlaneBitMask("NO_DATA"), numpy.inf)
        return exp

    def getWarpTypeList(self):
        """Return list of requested warp types per the config.
        """
        warpTypeList = []
        if self.config.makeDirect:
            warpTypeList.append("direct")
        if self.config.makePsfMatched:
            warpTypeList.append("psfMatched")
        return warpTypeList
