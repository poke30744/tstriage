import pytest
from pathlib import Path
from tscutter.ffmpeg import InputFile
from tstriage.epg import EPG

videoPath = Path(r"C:\Samples\2020年05月23日18時00分00秒-名探偵コナン「小五郎はBARにいる(前編)」[解][字][デ]_HD-1.ts")

def test_Dump():
    EPG.Dump(videoPath, videoPath.with_suffix('.epg'))
    epgPath = videoPath.with_suffix('.epg')
    assert epgPath.exists()
    epgPath.unlink()

def test_OutputDesc():
    EPG.Dump(videoPath, videoPath.with_suffix('.epg'))
    epgPath = videoPath.with_suffix('.epg')
    assert epgPath.exists()
    epg = EPG(epgPath, InputFile(videoPath))
    txtPath = epgPath.with_suffix('.txt')
    epg.OutputDesc(txtPath)
    assert txtPath.exists()
    epgPath.unlink()
    txtPath.unlink()

def test_ServiceId_and_Channel():
    EPG.Dump(videoPath, videoPath.with_suffix('.epg'))
    epgPath = videoPath.with_suffix('.epg')
    assert epgPath.exists()
    epg = EPG(epgPath, InputFile(videoPath))
    epgPath.unlink()
    assert epg.ServiceId() is not None
    assert epg.Channel() is not None
