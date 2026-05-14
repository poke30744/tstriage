#!/usr/bin/env python3
import json, os, socket, sys
from itertools import chain
from pathlib import Path
import logging
import unicodedata
import psutil
import click
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Column
from . import __version__
from . import cli_config
from ._progress import SubprocessProgress, _UnitColumn

console = Console(width=None if sys.stderr.isatty() else sys.maxsize)
from .epgstation import EPGStation
from .tasks import Encode, Index, Mark, Cut, Confirm, Cleanup
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
                self.CreateActionItem(newItem, '.toencode')
                logger.info(f'Will process: {item["path"]}')
            else:
                logger.warning(f'More information is needed: {item["path"]}')

    def Index(self):
        paths = list(self.nas.ActionItems('.toindex'))
        suffix = '.toindex'
        with RichProgress(
            SpinnerColumn(), TextColumn("{task.description}", table_column=Column(overflow="ellipsis")), _UnitColumn(), BarColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
            console=console, transient=False, refresh_per_second=10
        ) as rich:
            file_task = rich.add_task("Index", total=len(paths))
            for path in paths:
                item = self.LoadActionItem(path)
                name = Path(item['path']).stem
                rich.update(file_task, description=f"Index: {name}")
                original = path
                path = path.rename(path.with_suffix(f'{suffix}.{socket.gethostname()}'))
                try:
                    progress = SubprocessProgress(rich, ctx=name)
                    Index(item=item, epgStation=self.epgStation, quiet=self.quiet, progress=progress)
                    path.unlink()
                    self.CreateActionItem(item, '.tomark')
                except KeyboardInterrupt:
                    path.rename(original)
                    raise
                except:
                    logger.exception(f'in indexing "{path}":')
                    path.rename(path.with_suffix('.error'))
                    raise
                rich.advance(file_task)

    def Mark(self):
        paths = list(self.nas.ActionItems('.tomark'))
        suffix = '.tomark'
        with RichProgress(
            SpinnerColumn(), TextColumn("{task.description}", table_column=Column(overflow="ellipsis")), _UnitColumn(), BarColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
            console=console, transient=False, refresh_per_second=10
        ) as rich:
            file_task = rich.add_task("Mark", total=len(paths))
            for path in paths:
                item = self.LoadActionItem(path)
                name = Path(item['path']).stem
                rich.update(file_task, description=f"Mark: {name}")
                original = path
                path = path.rename(path.with_suffix(f'{suffix}.{socket.gethostname()}'))
                try:
                    progress = SubprocessProgress(rich, ctx=name)
                    Mark(item=item, epgStation=self.epgStation, quiet=self.quiet, progress=progress)
                    path.unlink()
                    self.CreateActionItem(item, '.tocut')
                except KeyboardInterrupt:
                    path.rename(original)
                    raise
                except:
                    logger.exception(f'in marking "{path}":')
                    path.rename(path.with_suffix('.error'))
                    raise
                rich.advance(file_task)

    def Cut(self):
        paths = list(self.nas.ActionItems('.tocut'))
        suffix = '.tocut'
        with RichProgress(
            SpinnerColumn(), TextColumn("{task.description}", table_column=Column(overflow="ellipsis")), _UnitColumn(), BarColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
            console=console, transient=False, refresh_per_second=10
        ) as rich:
            file_task = rich.add_task("Cut", total=len(paths))
            for path in paths:
                item = self.LoadActionItem(path)
                name = Path(item['path']).stem
                rich.update(file_task, description=f"Cut: {name}")
                outputFolder = path.with_suffix("")
                original = path
                path = path.rename(path.with_suffix(f'{suffix}.{socket.gethostname()}'))
                try:
                    progress = SubprocessProgress(rich, ctx=name)
                    Cut(item=item, outputFolder=outputFolder, quiet=self.quiet, progress=progress)
                    path.unlink()
                    self.CreateActionItem(item, '.toconfirm')
                except KeyboardInterrupt:
                    path.rename(original)
                    raise
                except:
                    logger.exception(f'in cutting "{path}":')
                    path.rename(path.with_suffix('.error'))
                    raise
                rich.advance(file_task)

    def Encode(self):
        paths = list(self.nas.ActionItems('.toencode'))
        suffix = '.toencode'
        with RichProgress(
            SpinnerColumn(), TextColumn("{task.description}", table_column=Column(overflow="ellipsis")), _UnitColumn(), BarColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
            console=console, transient=False, refresh_per_second=10
        ) as rich:
            file_task = rich.add_task("Encode", total=len(paths))
            for path in paths:
                item = self.LoadActionItem(path)
                name = Path(item['path']).stem
                rich.update(file_task, description=f"Encode: {name}")
                original = path
                path = path.rename(path.with_suffix(f'{suffix}.{socket.gethostname()}'))
                try:
                    progress = SubprocessProgress(rich, ctx=name)
                    Encode(item=item, epgStation=self.epgStation, encoder=self.encoder, presets=self.presets, quiet=self.quiet, progress=progress)
                    path.unlink()
                    self.CreateActionItem(item, '.toindex')
                except KeyboardInterrupt:
                    path.rename(original)
                    raise
                except:
                    logger.exception(f'in encoding "{path}":')
                    path.rename(path.with_suffix('.error'))
                    raise
                rich.advance(file_task)

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
                elif task == 'index':
                    self.Index()
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

@click.group(context_settings={'help_option_names': ['-h', '--help']})
@click.option('--config', '-c', default='tstriage.config.yml', show_default=True, help='Configuration file path')
@click.option('--quiet', '-q', is_flag=True, help='Suppress non-error output')
@click.option('--verbose', '-v', is_flag=True, help='Enable debug output')
@click.version_option(__version__, prog_name='tstriage', message='%(prog)s %(version)s')
@click.pass_context
def cli(ctx, config, quiet, verbose):
    """MPEG TS Triage Runner — batch processing pipeline for TV broadcast TS files.

    Processes recorded TS files through the pipeline:

    categorize -> list -> index -> mark -> cut -> encode -> confirm -> cleanup

    \b
    Examples:
      tstriage run categorize list index mark cut encode confirm cleanup
      tstriage categorize                             # single task
    """
    if verbose:
        log_level = logging.DEBUG
    elif quiet:
        log_level = logging.WARNING
    else:
        log_level = logging.INFO
    logging.basicConfig(
        level=log_level, format='%(message)s', datefmt='[%X]',
        handlers=[RichHandler(console=console, rich_tracebacks=sys.stderr.isatty())])

    ctx.ensure_object(dict)
    ctx.obj['config'] = config
    ctx.obj['quiet'] = quiet


def _load_config(ctx):
    """Load and prepare configuration from YAML file."""
    config_path = Path(ctx.obj['config'])
    with config_path.open(encoding='utf-8') as f:
        configuration = yaml.safe_load(f)
    configuration = _expand_env_vars(configuration)
    _inject_env_vars(configuration)
    return configuration


def _run_tasks(ctx, tasks):
    """Create Runner and execute the given tasks."""
    configuration = _load_config(ctx)
    Runner(configuration, quiet=ctx.obj['quiet']).Run(tasks)


@cli.command()
@click.pass_context
def categorize(ctx):
    """Match unprocessed TS files against EPGStation keywords, create .categorized items."""
    _run_tasks(ctx, ['categorize'])


@cli.command(name='list')
@click.pass_context
def list_cmd(ctx):
    """Convert .categorized items to .toencode using tstriage.json settings."""
    _run_tasks(ctx, ['list'])


@cli.command()
@click.pass_context
def index(ctx):
    """Run tscutter index on encoded MKV, create .tomark items."""
    _run_tasks(ctx, ['index'])


@cli.command()
@click.pass_context
def mark(ctx):
    """Run tsmarker mark (subtitles/logo/clipinfo/speech), create .tocut items."""
    _run_tasks(ctx, ['mark'])


@cli.command()
@click.pass_context
def cut(ctx):
    """Cut CM segments via tsmarker cut, create .toconfirm items."""
    _run_tasks(ctx, ['cut'])


@cli.command()
@click.pass_context
def encode(ctx):
    """Encode TS to MKV + extract EPG/logo/ASS, create .toindex items."""
    _run_tasks(ctx, ['encode'])


@cli.command()
@click.pass_context
def confirm(ctx):
    """Review encoded output with tsmarker groundtruth, decide re-encode or cleanup."""
    _run_tasks(ctx, ['confirm'])


@cli.command()
@click.pass_context
def cleanup(ctx):
    """Remove temporary cache files for completed items."""
    _run_tasks(ctx, ['cleanup'])


@cli.command()
@click.argument('tasks', nargs=-1, required=True, type=click.Choice([
    'categorize', 'list', 'encode', 'index', 'mark', 'cut', 'confirm', 'cleanup'
]))
@click.pass_context
def run(ctx, tasks):
    """Run multiple pipeline tasks in sequence.

    TASKS: one or more task names to execute in order.
    Example: tstriage run categorize list encode index mark cut confirm cleanup
    """
    _run_tasks(ctx, list(tasks))


def main():
    cli()


if __name__ == "__main__":
    main()