import pytest
from pathlib import Path
from tstriage.common import LoadExistingData, ExtractProgramList, ExtractProgram

videoPath = r"C:\Samples\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ts"
indexPath = r"C:\Samples\_metadata\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ptsmap"
markerPath = r"C:\Samples\_metadata\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.markermap"

outputPath = r"C:\Samples\conanProgram.ts"

def test_ExtractProgramList():    
    _, markerMap = LoadExistingData(Path(indexPath), Path(markerPath))
    programList = ExtractProgramList(markerMap, byGroup=False)
    assert len(programList) == 1

def test_ExtractProgramList_ByGroup():
    _, markerMap = LoadExistingData(Path(indexPath), Path(markerPath))
    programList = ExtractProgramList(markerMap, byGroup=True)
    assert len(programList) > 1

def test_ExtractProgram():
    ptsMap, markerMap = LoadExistingData(Path(indexPath), Path(markerPath))
    programList = ExtractProgramList(markerMap, byGroup=False)
    ExtractProgram(videoPath=videoPath, clips=programList[0], ptsMap=ptsMap, outputPath=outputPath)

def test_ExtractProgram_ByGroup():
    ptsMap, markerMap = LoadExistingData(Path(indexPath), Path(markerPath))
    programList = ExtractProgramList(markerMap, byGroup=True)
    for i in range(len(programList)):
        ExtractProgram(videoPath=videoPath, clips=programList[i], ptsMap=ptsMap,
            outputPath=Path(outputPath).with_stem(f'{Path(outputPath).stem}_{i}'))