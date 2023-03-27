import logging
import re
from pathlib import Path
from typing import Optional

MINIMUM_SUPPORTED_PYTHON_MINOR = 7

logger = logging.getLogger(__name__)
user_agent = "pythonscript/0.0.1-dev1+cat <konstin@mailbox.org>"
base_dir = Path(__file__).parent.parent
default_cache_dir = base_dir.joinpath("cache")
resolutions_ours = base_dir.joinpath("resolutions_ours")
resolutions_pip = base_dir.joinpath("resolutions_pip")
normalizer = re.compile(r"[-_.]+")


def normalize(name):
    return normalizer.sub("-", name).lower()


class Cache:
    """Quick and simple cache abstraction that can be turned off for the tests"""

    root_cache_dir: Path
    read: bool
    write: bool

    def __init__(self, root_cache_dir: Path, read: bool = True, write: bool = True):
        self.root_cache_dir = root_cache_dir
        self.read = read
        self.write = write

    def filename(self, bucket: str, name: str) -> Path:
        return self.root_cache_dir.joinpath(bucket).joinpath(name)

    def get(self, bucket: str, name: str) -> Optional[str]:
        if not self.read:
            return None

        filename = self.filename(bucket, name)
        # Avoid an expensive is_file call
        try:
            return filename.read_text()
        except FileNotFoundError:
            return None

    def set(self, bucket: str, name: str, content: str):
        if not self.write:
            return False
        filename = self.filename(bucket, name)
        filename.parent.mkdir(exist_ok=True, parents=True)
        filename.write_text(content)
