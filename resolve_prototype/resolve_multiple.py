"""Resolves multiple requirements independent of each other, for profiling and
benchmarking"""

import asyncio
import logging
from argparse import ArgumentParser

from pypi_types.pep440_rs import VersionSpecifier
from pypi_types.pep508_rs import Requirement
from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.resolve import Resolution, resolve, freeze


def main():
    logging.basicConfig(level=logging.WARNING)
    logging.captureWarnings(True)
    requires_python = VersionSpecifier(">= 3.7")

    parser = ArgumentParser()
    parser.add_argument("requirement", nargs="+")
    args = parser.parse_args()
    print(args.requirement)

    for _ in range(10):
        for requirement in args.requirement:
            root_requirement = Requirement(requirement)
            resolution: Resolution = asyncio.run(
                resolve(root_requirement, requires_python, Cache(default_cache_dir))
            )
            freeze(resolution, root_requirement)


if __name__ == "__main__":
    main()
