#!/usr/bin/env python3
import argparse, json, os, socket
import shutil
from itertools import chain
from pathlib import Path
import logging
import unicodedata
import psutil
import yaml
from . import cli_config
from .epgstation import EPGStation
from .tasks import Analyze, Mark, Cut, Encode, Confirm, Cleanup
from .nas import NAS

logger = logging.getLogger('tstriage.runner')

class Runner:
    def __init__(self, configuration, quiet: bool):
        self.configuration = configuration
        self.quiet = quiet
        cli = configuration.get('Cli', {})
        cli_config.configure(
            tscutter=cli.get('tscutter', ''),
            tsmarker=cli.get('tsmarker', ''),
        )
        self.encoder = configuration['Encoder']
        self.presets = configuration['Presets']
        self.epgStation = EPGStation(url=configuration['EPGStation'])
        self.nas = NAS(
            recorded=Path(self.configuration['Uncategoried']).expanduser(),
            destination=Path(configuration['Destination']).expanduser())
    
    # wait for other instances to finish
    def SingleInstanceWait(self):
        allProcesses = psutil.process_iter(attrs=['pid', 'name'])
        currentProcess = psutil.Process()
        for process in allProcesses:
            if process.info['name'] == currentProcess.name and process.info['pid'] != currentProcess.pid: # type: ignore
                logger.info(f'waiting for process {process.info["pid"]} to finish ...') # type: ignore
                process.wait()

    def Categorize(self):
        for path in self.nas.SearchUnprocessedFiles():
            destination = None
            for keyword in sorted(self.epgStation.GetKeywords(), key=len, reverse=True):
                if unicodedata.normalize('NFKC', keyword) in unicodedata.normalize('NFKC', path.stem):
                    epg = self.epgStation.GetEPG(path)
                    if epg is None:
                        break
                    with (Path(__file__).parent / 'event.yml').open(encoding='utf-8') as f:        
                        eventDesc = yaml.load(f, Loader=yaml.FullLoader)
                        genre = epg['genre1'] if 'genre1' in epg else epg['genre2']
                    genreDesc = eventDesc['Genre'][str(genre)]
                    destination = self.nas.destination / genreDesc / keyword
                    break
            item = {
                'path': str(path),
                'destination': str(destination) if destination is not None else None,
            }
            self.CreateActionItem(item, '.categorized')

    def LoadActionItem(self, path: Path) -> dict[str, str]:
        with path.open() as f:
            item = json.load(f)
        # fix pathes
        item['path'] = str(self.nas.recorded / item['path'])
        item['destination'] = (str(self.nas.destination / item['destination'])) if item['destination'] != 'None' else item['destination']
        if os.name == 'nt':
            item['path'] = item['path'].replace('/', '\\')
            item['destination'] = item['destination'].replace('/', '\\')
        else:
            item['path'] = item['path'].replace('\\', '/')
            item['destination'] = item['destination'].replace('\\', '/')
        return item
    
    def CreateActionItem(self, item, suffix: str) -> Path:
        self.nas.tstriageFolder.mkdir(parents=True, exist_ok=True)
        actionItemPath = self.nas.tstriageFolder / Path(item['path']).with_suffix(suffix).name
        item['path'] = str(Path(item['path']).relative_to(self.nas.recorded))
        item['destination'] = str(Path(item['destination']).relative_to(self.nas.destination)) if item['destination'] is not None else 'None'
        with actionItemPath.open('w') as f:
            json.dump(item, f, ensure_ascii=False, indent=True)
        return actionItemPath
    
    def List(self):
        for path in self.nas.ActionItems('.categorized'):
            item = self.LoadActionItem(path)
            encodeTo = item["destination"]
            if encodeTo != 'None':
                with self.nas.FindTsTriageSettings(folder=Path(encodeTo)).open() as f:
                    settings = json.load(f)
                path.unlink()
                newItem = {
                    'path': item['path'],
                    'destination': item['destination'],
                    'cutter': settings.get('cutter', {}),
                    'marker': settings.get('marker', {}),
                    'encoder': settings.get('encoder', {})
                }
                self.CreateActionItem(newItem, '.toanalyze')
                logger.info(f'Will process: {item["path"]}')
            else:
                logger.warn(f'More information is needed: {item["path"]}')

    def Analyze(self):
        for path in self.nas.ActionItems('.toanalyze'):
            item = self.LoadActionItem(path)
            path = path.rename(path.with_suffix(f'.toanalyze.{socket.gethostname()}'))
            try:
                Analyze(item=item, epgStation=self.epgStation, quiet=self.quiet)
                path.unlink()
                self.CreateActionItem(item, '.tomark')
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in analyzing "{path}":')
                path.rename(path.with_suffix('.error'))
                raise

    def Mark(self):
        for path in self.nas.ActionItems('.tomark'):
            item = self.LoadActionItem(path)
            path = path.rename(path.with_suffix(f'.tomark.{socket.gethostname()}'))
            try:
                Mark(item=item, epgStation=self.epgStation, quiet=self.quiet)
                path.unlink()
                self.CreateActionItem(item, '.tocut')
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in marking "{path}":')
                path.rename(path.with_suffix('.error'))
                raise

    def Cut(self):
        for path in self.nas.ActionItems('.tocut'):
            item = self.LoadActionItem(path)
            outputFolder = path.with_suffix("")
            path = path.rename(path.with_suffix(f'.tocut.{socket.gethostname()}'))
            try:
                Cut(item=item, outputFolder=outputFolder, quiet=self.quiet)
                path.unlink()
                self.CreateActionItem(item, '.toencode')
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in cutting "{path}":')
                path.rename(path.with_suffix('.error'))
                raise

    def Encode(self):
        for path in self.nas.ActionItems('.toencode'):
            item = self.LoadActionItem(path)
            path = path.rename(path.with_suffix(f'.toencode.{socket.gethostname()}'))
            try:
                Encode(item=item, encoder=self.encoder, presets=self.presets, quiet=self.quiet)
                path.unlink()
                metadataFolder = Path(item['destination']) / '_metadata'
                newTriagePath = self.CreateActionItem(item, '.toconfirm')
                shutil.copy(newTriagePath, metadataFolder / newTriagePath.with_suffix('.toencode').name)
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in encoding "{path}":')
                path.rename(path.with_suffix('.error'))
                raise

    def Confirm(self):
        for path in chain(self.nas.ActionItems('.toencode'), self.nas.ActionItems('.toconfirm'), self.nas.ActionItems('.tocleanup')):
            item = self.LoadActionItem(path)
            outputFolder = path.with_suffix("")
            reEncodingNeeded = Confirm(item=item, outputFolder=outputFolder)
            path.unlink()
            if reEncodingNeeded or path.suffix == '.toencode':
                self.CreateActionItem(item, '.toencode')
            else:
                self.CreateActionItem(item, '.tocleanup')

    def Cleanup(self):
        for path in self.nas.ActionItems('.tocleanup'):
            item = self.LoadActionItem(path)
            Cleanup(item=item)
    
    def Run(self, tasks):
        self.SingleInstanceWait()

        logger.info(f'running {tasks} ...')
        for task in tasks:
            try:
                if task == 'categorize':
                    self.Categorize()
                elif task == 'list':
                    self.List()
                elif task == 'analyze':
                    self.Analyze()
                elif task == 'mark':
                    self.Mark()
                elif task == 'cut':
                    self.Cut()
                elif task == 'encode':
                    self.Encode()
                elif task == 'confirm':
                    self.Confirm()
                elif task == 'cleanup':
                    self.Cleanup()
            except KeyboardInterrupt:
                logger.info('interrupted by user')
                break
            except FileNotFoundError:
                logger.warning(f'File not found during {task} task. Please check the configuration and input files.')
                continue

def _expand_env_vars(obj):
    """Recursively expand environment variables in configuration values.

    Supports both ${VAR_NAME} and $VAR_NAME syntax.
    """
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(v) for v in obj]
    elif isinstance(obj, str):
        # Expand environment variables in string
        return os.path.expandvars(obj)
    else:
        return obj

def _inject_env_vars(configuration):
    """Inject environment variables from configuration.

    Looks for 'Environment', 'Env', or 'environment' section in configuration
    and sets those key-value pairs as environment variables.
    """
    # Try different possible keys for environment section
    env_section = None
    for key in ['Environment', 'Env', 'environment', 'env']:
        if key in configuration:
            env_section = configuration[key]
            break

    if env_section and isinstance(env_section, dict):
        for key, value in env_section.items():
            if isinstance(value, (str, int, float, bool)):
                # Convert to string for environment variable
                os.environ[key] = str(value)
                logger.info(f'Set environment variable: {key}')
            elif value is None:
                # Remove environment variable if value is None
                if key in os.environ:
                    del os.environ[key]
                    logger.info(f'Removed environment variable: {key}')

def main():
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', default='tstriage.config.yml', help='configuration file path')
    parser.add_argument('--quiet', '-q', action='store_true', default=False, help='disable progress bar')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['categorize', 'list', 'analyze', 'mark', 'cut', 'confirm', 'encode', 'cleanup'], help='tasks to run')

    args = parser.parse_args()

    log_level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    configurationPath = Path(args.config)
    with configurationPath.open(encoding='utf-8') as f:
        configuration = yaml.safe_load(f)

    # Expand environment variables in configuration
    configuration = _expand_env_vars(configuration)

    # Inject environment variables from configuration
    _inject_env_vars(configuration)

    runner = Runner(configuration, quiet=args.quiet)
    runner.Run(args.task)

if __name__ == "__main__":
    main()