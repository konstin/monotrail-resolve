import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
user_agent = "pythonscript/0.0.1-dev1+cat <konstin@mailbox.org>"
base_dir = Path(__file__).parent.parent
default_cache_dir = base_dir.joinpath("cache")
resolutions_ours = base_dir.joinpath("resolutions_ours")
resolutions_pip = base_dir.joinpath("resolutions_pip")
normalizer = re.compile(r"[-_.]+")


def normalize(name):
    return normalizer.sub("-", name).lower()


def filename_to_version(package_name: str, filename: str) -> Optional[str]:
    """
    So distribution filenames are a bit wacky: While wheels always have the same
    structure (ends with `.whl`, five parts in the stem separated by four `-` of which
    the second is the version), sdist have only been specified in 2020. Before that,
    filenames may be kinda ambiguous in the sense that `tokenizer-rt-1.0-final1.tar.gz`
    is valid as well as `tokenizer-1.0.tar.gz`. That's why we try to match the suffix
    `.tar.gz` and the prefix by normalizing package name and the same length in the
    filename by https://peps.python.org/pep-0503/#normalized-names and then parse the
    version out of the middle.
    """
    if filename.endswith(".whl"):
        return filename.split("-", maxsplit=2)[1]
    # sdist
    # Older packages (such as word2number 1.1 and pytz 2016.10) might have only .zip or
    # .tar.bz2 sdists respectively
    elif (
        filename.endswith(".tar.gz")
        or filename.endswith(".zip")
        or filename.endswith(".tar.bz2")
        or filename.endswith(".tgz")
    ):
        # https://peps.python.org/pep-0503/#normalized-names
        package_name_normalized = normalize(package_name).lower()
        file_prefix = normalize(filename[: len(package_name)]).lower()
        if not package_name_normalized == file_prefix:
            raise RuntimeError(
                f"Name mismatch: '{package_name}' expected,"
                f" '{filename[:len(package_name)]}' found (normalized '{package_name}'"
                f" vs '{file_prefix}')"
            )
        assert filename[len(package_name)] == "-"
        # python 3.8 does not have removesuffix :/
        # len prefix plus one minus
        base_name = filename[len(package_name) + 1 :]
        archive_suffixes = [".tar.gz", ".zip", ".tar.bz2", ".tgz"]
        for suffix in archive_suffixes:
            base_name = base_name.replace(suffix, "")
        return base_name
    # These are known, but we don't use them
    elif (
        filename.endswith(".exe")
        or filename.endswith(".msi")
        or filename.endswith(".egg")
        or filename.endswith(".rpm")
    ):
        return None
    else:
        logger.warning(f"File with unexpected name in {package_name}: {filename}")
        return None


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
