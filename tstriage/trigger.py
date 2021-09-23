import time, logging, sched, argparse, json
from pathlib import Path
from datetime import datetime, timedelta
import threading
from getch import getch
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .runner import Runner
from .common import WindowsInhibitor

class RecorderFileEventHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        # default to 1 day ago
        self.lastWriteTime = datetime.now() - timedelta(days=1)
    
    def on_moved(self, event):
        super().on_moved(event)
        self.OnFileEvent()

    def on_created(self, event):
        super().on_created(event)
        self.OnFileEvent()

    def on_deleted(self, event):
        super().on_deleted(event)
        self.OnFileEvent()

    def on_modified(self, event):
        super().on_modified(event)
        self.OnFileEvent()

    def OnFileEvent(self):
        self.lastWriteTime = datetime.now()

class Trigger:
    def __init__(self, runner):
        self.runner = runner
        self.lastProcessedTimestamp = None

        # watchdog
        self.observer = Observer()
        self.eventHandler = RecorderFileEventHandler()
        self.observer.schedule(self.eventHandler, path=runner.configuration['Uncategoried'], recursive=False)

        # keyboard
        self.exiting = False
        self.keyboardThread = threading.Thread(target=self.KeyboardWorker)
        self.keyboardThread.start()

        # scheduler
        self.scheduler = sched.scheduler(time.time, time.sleep)
        self.scheduler.enter(delay=1, priority=1, action=self.CheckRecorderEvent)
    
    def KeyboardWorker(self):
        while not self.exiting:
            ch = getch()
            if ch == b'x':
                self.exiting = True
            elif ch == b'c':
                self.runner.Run(tasks=('cleanup', 'confirm', 'cleanup'))
            elif ch == b'r':
                self.eventHandler.OnFileEvent()

    def CheckRecorderEvent(self):
        if all((self.eventHandler.lastWriteTime < datetime.now() - timedelta(minutes=1), not self.lastProcessedTimestamp == self.eventHandler.lastWriteTime)):
            self.lastProcessedTimestamp = self.eventHandler.lastWriteTime
            self.runner.Run(tasks=('categorize', 'list', 'mark', 'encode'))
        if not self.exiting:
            self.scheduler.enter(delay=1, priority=1, action=self.CheckRecorderEvent)

    def Run(self):
        print('Press "x" to exit, "r" to re-run, "c" to confirm marking results ...')
        self.observer.start()
        with WindowsInhibitor() as wi:
            self.scheduler.run()
        print('Exiting ...')
        self.observer.stop()
        self.observer.join()
        self.keyboardThread.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Python script to automate TS tasks')
    parser.add_argument('--config', '-c', required=True, help='configuration file path')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("debug.log"),
            logging.StreamHandler()
    ])

    configurationPath = Path(args.config)
    with configurationPath.open() as f:
        configuration = json.load(f)
    
    runner = Runner(configuration)
    trigger = Trigger(runner)
    trigger.Run()
