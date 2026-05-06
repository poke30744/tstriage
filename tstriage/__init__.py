from importlib.metadata import version as _version

try:
    __version__ = _version("tstriage")
except Exception:
    import os

    __version__ = f"0.1.{os.getenv('BUILD_NUMBER', '0')}"
