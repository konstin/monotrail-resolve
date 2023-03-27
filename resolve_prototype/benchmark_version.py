import sys
import time
from collections import defaultdict

import orjson
import pypi_types
from pypi_types import pep440_rs, filename_to_version

from resolve_prototype.common import default_cache_dir


def main():
    start = time.time()
    for file in default_cache_dir.joinpath("pypi_simple_releases").iterdir():
        invalid_versions = []
        invalid_filenames = []
        releases = defaultdict(list)
        data = orjson.loads(file.read_bytes())
        for pypi_file in data["files"]:
            if version := filename_to_version(file.stem, pypi_file["filename"]):
                try:
                    version = pep440_rs.Version(version)
                except ValueError:
                    invalid_versions.append(version)
                    continue
                releases[version].append(pypi_file["filename"])
            else:
                invalid_filenames.append(pypi_file["filename"])
    end = time.time()

    start2 = time.time()
    for file in default_cache_dir.joinpath("pypi_simple_releases").iterdir():
        pypi_types.parse_releases_data(file.stem, str(file))
    end2 = time.time()

    print(f"{end - start:.2}s {end2 - start2:.2}s")


if __name__ == "__main__":
    start = time.time()
    if len(sys.argv) == 2:
        for _ in range(int(sys.argv[1])):
            main()
    else:
        main()
    end = time.time()
    print(f"{end - start:.2}s")
