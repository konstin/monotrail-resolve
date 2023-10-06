import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from subprocess import check_output
from collections.abc import Iterable, Awaitable
from typing import Any

from build import BuildBackendException
from httpx import AsyncHTTPTransport
from tqdm import tqdm

from pypi_types.pep440_rs import VersionSpecifiers
from pypi_types.pep508_rs import Requirement
from resolve_prototype import resolve_requirement, Cache, default_cache_dir
from resolve_prototype.resolve import freeze

logger = logging.getLogger(__name__)

root = Path(check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())


def as_completed_with_limit(
    n: int, coros: list[tuple[str, Awaitable]]
) -> Iterable[Awaitable[tuple[str, Any]]]:
    """https://stackoverflow.com/a/61478547/3549270"""
    semaphore = asyncio.Semaphore(n)

    async def sem_coro(name: str, coro) -> tuple[str, Any]:
        async with semaphore:
            return name, await coro

    return asyncio.as_completed(list(sem_coro(name, c) for (name, c) in coros))


async def single(failures, idx, project, requires_python, top_packages, transport):
    logger.info(f"Resolving {idx + 1}/{len(top_packages)} {project}")
    try:
        start = time.time()
        resolution = await resolve_requirement(
            # TODO: Force latest version
            Requirement(project),
            requires_python,
            Cache(default_cache_dir, refresh_versions=False),
            transport=transport,
        )
        end = time.time()
        logging.info(f"{project} {end - start:.3f}s")
        logger.info(freeze(resolution, Requirement(project)))
    except (RuntimeError, ValueError, BuildBackendException) as e:
        failures.append(project)
        print(e)


async def main():
    pypi_top_json = root.joinpath("download").joinpath(
        "top-pypi-packages-30-days.min.json"
    )
    top_packages = [i["project"] for i in json.loads(pypi_top_json.read_text())["rows"]]
    requires_python = VersionSpecifiers(">=3.8,<3.12")
    transport = AsyncHTTPTransport(retries=3)
    failures = []

    """
    tasks = [
        (
            project,
            resolve_requirement(
                # TODO: Force latest version
                Requirement(project),
                requires_python,
                Cache(default_cache_dir, refresh_versions=False),
            ),
        )
        for project in top_packages
    ]
    for done in tqdm(as_completed_with_limit(1, tasks), total=len(top_packages)):
        try:
            name, result = await done
            # print(name)
        except (RuntimeError, ValueError, HTTPError, BuildBackendException):
            pass
    """

    all_start = time.time()
    for idx, project in enumerate(tqdm(top_packages, file=sys.stdout)):
        await single(failures, idx, project, requires_python, top_packages, transport)
    # batch_size = 100
    # for batch_start in tqdm(
    #    list(range(len(top_packages) // batch_size)), file=sys.stdout
    # ):
    #    await asyncio.gather(
    #        *[
    #            single(
    #                failures,
    #                batch_start + idx,
    #                package,
    #                requires_python,
    #                top_packages,
    #                transport,
    #            )
    #            for idx, package in enumerate(
    #                top_packages[batch_start : batch_start + batch_size]
    #            )
    #        ]
    #    )
    all_stop = time.time()
    print(f"{all_stop-all_start}s")
    print(f"{len(failures)}: {failures}")


if __name__ == "__main__":
    asyncio.run(main())
