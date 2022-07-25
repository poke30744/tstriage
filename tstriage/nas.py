from genericpath import isfile
import json, unicodedata
from pathlib import Path
from .epgstation import EPGStation

class NAS:
    def __init__(self, recorded: Path, destination: Path, epgStation: EPGStation=None) -> None:
        self.recorded = recorded
        self.destination = destination
        self.epgStation = epgStation
        self.tstriageFolder = recorded / '_tstriage'
        self.encodedFilesPath = self.tstriageFolder / 'encodedFiles.txt'

    def __RefreshNAS(self, force: bool=False):
        if not force:
            if self.encodedFilesPath.exists():
                return
        if self.epgStation is not None:
            self.epgStation.BusyWait()
        if not self.tstriageFolder.exists():
            self.tstriageFolder.mkdir()
        allVideoFiles = []
        for path in Path(self.destination).glob('**/*'):
            if path.suffix in ('.mp4',) and (path.parent / '_metadata').exists():
                allVideoFiles.append(path)
        encodedFiles = []
        for path in Path(self.recorded).glob('*'):
            if path.is_file():
                encoded = False
                for p2 in allVideoFiles:
                    if path.stem in p2.stem or p2.stem in path.stem:
                        encoded = True
                        break
                if encoded:
                    encodedFiles.append(p2.name)
        with self.encodedFilesPath.open('w') as f:
            json.dump(sorted([str(i) for i in encodedFiles]), f, ensure_ascii=False, indent=True)
    
    def EncodedFiles(self) -> list[str]:
        self.__RefreshNAS()
        with self.encodedFilesPath.open() as f:
            return json.load(f)

    def AddEncodedFile(self, path: Path) -> None:
        encodedFiles = self.EncodedFiles()
        if not path.name in encodedFiles:
            encodedFiles.append(path.name)
            with self.encodedFilesPath.open('w') as f:
                json.dump(encodedFiles, f, ensure_ascii=False, indent=True)

    def RecordedFiles(self) -> list:
        return [ path for path in Path(self.recorded).glob('*') if path.suffix in ('.ts', '.m2ts') ]

    def HadBeenEncoded(self, path) -> bool:
        for encodedFile in self.EncodedFiles():
            if path.stem in encodedFile:
                return True
        return False
    
    def ActionItems(self, suffix: str=None) -> list[Path]:
        actionItems = []
        for path in self.tstriageFolder.glob('*.*'):
            if not path.suffix in ['.ts', '.m2ts', '.txt']:
                if suffix is not None:
                    if not path.suffix == suffix:
                        continue
                actionItems.append(path)
        return actionItems

    def FindActionItem(self, path: Path) -> Path:
        for actionItemPath in self.ActionItems():
            if path.stem == actionItemPath.stem:
                return actionItemPath
        return None
    
    def HasActionItem(self, path: Path) -> bool:
        return self.FindActionItem(path) is not None
    
    def CreateActionItem(self, item, suffix: str) -> None:
        actionItemPath = self.tstriageFolder / Path(item['path']).with_suffix(suffix).name
        with actionItemPath.open('w') as f:
            json.dump(item, f, ensure_ascii=False, indent=True)
    
    def LoadActionItem(self, path: Path) -> dict[str: str]:
        with path.open() as f:
            return json.load(f)
    
    def FindTsTriageSettings(self, folder: Path) -> Path:
        settingsPath = folder / 'tstriage.json'
        if settingsPath.exists():
            return settingsPath
        elif folder == self.destination:
            defaultSettings = {
                "marker": {
                    "noEnsemble": True,
                },
                "encoder": {
                    "preset": "drama",
                },
            }
            with settingsPath.open('w') as f:
                json.dump(defaultSettings, f, indent=True)
            return settingsPath
        else:
            return self.FindTsTriageSettings(folder.parent)
