import shlex

_tscutter_cmd = 'tscutter'
_tsmarker_cmd = 'tsmarker'


def configure(tscutter: str = '', tsmarker: str = ''):
    global _tscutter_cmd, _tsmarker_cmd
    if tscutter:
        _tscutter_cmd = tscutter
    if tsmarker:
        _tsmarker_cmd = tsmarker


def tscutter(*args: str) -> list[str]:
    return shlex.split(_tscutter_cmd) + list(args)


def tsmarker(*args: str) -> list[str]:
    return shlex.split(_tsmarker_cmd) + list(args)
