"""Resolves multiple requirements independent of each other, for profiling and
benchmarking"""

import asyncio
import logging
from argparse import ArgumentParser

from httpx import AsyncHTTPTransport

from pypi_types.pep440_rs import VersionSpecifiers
from pypi_types.pep508_rs import Requirement
from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.resolve import Resolution, resolve_requirement, freeze


def main():
    logging.basicConfig(level=logging.WARNING)
    logging.captureWarnings(True)
    requires_python = VersionSpecifiers(">=3.7,<3.12")

    parser = ArgumentParser()
    parser.add_argument("requirement", nargs="+")
    args = parser.parse_args()

    transport = AsyncHTTPTransport(retries=3)

    for i in range(30):
        print(i)
        for requirement in args.requirement:
            root_requirement = Requirement(requirement)
            resolution: Resolution = asyncio.run(
                resolve_requirement(
                    root_requirement,
                    requires_python,
                    Cache(default_cache_dir),
                    transport=transport,
                )
            )
            freeze(resolution, root_requirement)


if __name__ == "__main__":
    main()
