import logging

logger = logging.getLogger('tstriage.tee')


class Tee:
    """Write data from a stream to multiple pipes.

    Usage:
        tee = Tee(strip.stdin, subtitles.stdin, broken_ok=(subtitles.stdin,))
        tee.pump(extract_proc.stdout)
    """

    def __init__(self, *pipes, broken_ok: tuple = ()):
        self.pipes = pipes
        self.broken_ok = set(broken_ok)

    def pump(self, stream, buf_size: int = 1024 * 1024):
        while chunk := stream.read(buf_size):
            self.write(chunk)
        self.close()

    def write(self, data):
        broken = set()
        for p in self.pipes:
            if p in broken:
                continue
            try:
                p.write(data)
            except (BrokenPipeError, OSError):
                if p in self.broken_ok:
                    broken.add(p)
                else:
                    raise

    def close(self):
        for p in self.pipes:
            p.close()
