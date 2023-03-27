"""Resolves multiple requirements independent of each other, for profiling and
benchmarking"""

import asyncio
import logging
import sys

from pep508_rs import Requirement, VersionSpecifier

from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.resolve import Resolution, resolve, freeze


def main():
    logging.basicConfig(level=logging.WARNING)
    logging.captureWarnings(True)
    requires_python = VersionSpecifier(">= 3.7")

    for _ in range(10):
        for requirement in sys.argv[1:]:
            root_requirement = Requirement(requirement)
            resolution: Resolution = asyncio.run(
                resolve(root_requirement, requires_python, Cache(default_cache_dir))
            )
            freeze(resolution, root_requirement)


if __name__ == "__main__":
    main()
