import pytest
from pathlib import Path
from tscutter.common import PtsMap
from tstriage.pipeline import MarkerMap

videoPath = Path(r"C:\Samples\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ts")
indexPath = Path(r"C:\Samples\_metadata\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ptsmap")
markerPath = Path(r"C:\Samples\_metadata\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.markermap")

outputPath = Path(r"C:\Samples\conanProgram.ts")

def test_ExtractProgramList():
    markerMap = MarkerMap(markerPath, PtsMap(indexPath))
    programList = markerMap.GetProgramClips()
    assert len(programList) == 9

def test_ExtractProgramList_ByGroup():
    markerMap = MarkerMap(markerPath, PtsMap(indexPath))
    programList = markerMap.GetProgramClips()
    mergedProgramList = MarkerMap.MergeNeighbors(programList)
    assert len(mergedProgramList) == 3