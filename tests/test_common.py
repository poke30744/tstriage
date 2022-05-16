import pytest
from pathlib import Path
from tscutter.common import PtsMap
from tstriage.common import MarkerMap, ExtractProgramList, ExtractProgram

videoPath = Path(r"C:\Samples\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ts")
indexPath = Path(r"C:\Samples\_metadata\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ptsmap")
markerPath = Path(r"C:\Samples\_metadata\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.markermap")

outputPath = Path(r"C:\Samples\conanProgram.ts")

def test_ExtractProgramList():
    markerMap = MarkerMap(markerPath, PtsMap(indexPath))
    programList = ExtractProgramList(markerMap, byGroup=False)
    assert len(programList) == 1

def test_ExtractProgramList_ByGroup():
    markerMap = MarkerMap(markerPath, PtsMap(indexPath))
    programList = ExtractProgramList(markerMap, byGroup=True)
    assert len(programList) > 1

def test_ExtractProgram():
    ptsMap = PtsMap(indexPath)
    markerMap = MarkerMap(markerPath, ptsMap)
    programList = ExtractProgramList(markerMap, byGroup=False)
    ExtractProgram(videoPath=videoPath, clips=programList[0], ptsMap=ptsMap.data, outputPath=outputPath)
    assert outputPath.exists()
    outputPath.unlink()

def test_ExtractProgram_ByGroup():
    ptsMap = PtsMap(indexPath)
    markerMap = MarkerMap(markerPath, ptsMap)
    programList = ExtractProgramList(markerMap, byGroup=True)
    for i in range(len(programList)):
        outputPathN = Path(outputPath).with_stem(f'{Path(outputPath).stem}_{i}')
        ExtractProgram(videoPath=videoPath, clips=programList[i], ptsMap=ptsMap.data, outputPath=outputPathN)