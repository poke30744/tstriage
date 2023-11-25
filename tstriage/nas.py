import json
from pathlib import Path
from typing import Generator, Optional
from tqdm import tqdm

class NAS:
    def __init__(self, recorded: Path, destination: Path):
        self.recorded = recorded
        self.destination = destination
        self.tstriageFolder = recorded / '_tstriage'
        self.encodedFilesPath = self.tstriageFolder / 'encodedFiles.txt'

    def __RefreshNAS(self, force: bool=False):
        if not force:
            if self.encodedFilesPath.exists():
                return
        if not self.tstriageFolder.exists():
            self.tstriageFolder.mkdir()
        allVideoFiles: list[Path] = []
        for path in tqdm(Path(self.destination).glob('**/*'), desc="Loading encoded files"):
            if path.suffix in ('.mp4',) and (path.parent / '_metadata').exists():
                allVideoFiles.append(path)
        encodedFiles = []
        for path in tqdm(Path(self.recorded).glob('*'), desc='loading recorded files'):
            if path.is_file():
                for p2 in allVideoFiles:
                    if path.stem in p2.stem or p2.stem in path.stem:
                        encodedFiles.append(p2.name)
                        break
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
    
    def ActionItems(self, suffix: Optional[str]=None) -> Generator[Path, None, None]:
        for path in self.tstriageFolder.glob('*.*'):
            if not path.suffix in ['.ts', '.m2ts', '.txt']:
                if suffix is not None:
                    if not path.suffix == suffix:
                        continue
                yield path
                
    def FindActionItem(self, path: Path) -> Optional[Path]:
        for actionItemPath in self.ActionItems():
            if path.stem in actionItemPath.stem:
                return actionItemPath
        return None
    
    def HasActionItem(self, path: Path) -> bool:
        return self.FindActionItem(path) is not None
    
    def CreateActionItem(self, item, suffix: str) -> None:
        actionItemPath = self.tstriageFolder / Path(item['path']).with_suffix(suffix).name
        with actionItemPath.open('w') as f:
            json.dump(item, f, ensure_ascii=False, indent=True)
    
    def LoadActionItem(self, path: Path) -> dict[str, str]:
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
