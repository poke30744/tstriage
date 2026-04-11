import os

__version__ = f"0.1.{os.getenv('BUILD_NUMBER', '0')}"
