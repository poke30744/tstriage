import json
from pathlib import Path
from typing import Generator, Optional
from tqdm import tqdm

class NAS:
    def __init__(self, recorded: Path, destination: Path):
        self.recorded = recorded
        self.destination = destination
        self.tstriageFolder = recorded / '_tstriage'
    
    def SearchUnprocessedFiles(self) -> list[Path]:
        processedFiles: set[str] = set()
        for path in tqdm(Path(self.destination).glob('**/*'), desc="Loading encoded files"):
            if path.suffix in ('.mp4',) and (path.parent / '_metadata').exists():
                processedFiles.add(path.stem.replace('_trimmed', '').replace('_prog', ''))

        unprocessedFiles: list[Path] = []
        for path in tqdm(Path(self.recorded).glob('*'), desc='loading recorded files'):
            if path.is_file():
                if not path.stem in processedFiles and path.suffix in ('.ts', '.m2ts'):
                    unprocessedFiles.append(path)
        return unprocessedFiles
    
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
