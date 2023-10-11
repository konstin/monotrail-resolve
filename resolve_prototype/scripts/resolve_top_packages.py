import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import json
import logging
import sys
import time
from collections.abc import Iterable, Awaitable
from pathlib import Path
from subprocess import check_output
from typing import Any

from build import BuildBackendException
from httpx import AsyncHTTPTransport, HTTPError
from tqdm import tqdm

from pypi_types.pep440_rs import VersionSpecifiers
from pypi_types.pep508_rs import Requirement
from resolve_prototype import resolve_requirement, Cache, default_cache_dir

logger = logging.getLogger(__name__)

root = Path(check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())


def as_completed_with_limit(
    n: int, coros: list[tuple[str, Awaitable[Any]]]
) -> Iterable[Awaitable[tuple[str, Any]]]:
    """https://stackoverflow.com/a/61478547/3549270"""
    semaphore = asyncio.Semaphore(n)

    async def sem_coro(name: str, coro) -> tuple[str, Any]:
        async with semaphore:
            return name, await coro

    return asyncio.as_completed(list(sem_coro(name, c) for (name, c) in coros))


pending = []


async def single(
    failures: list[str],
    idx: int,
    project: str,
    requires_python: VersionSpecifiers,
    top_packages: list,
    transport: AsyncHTTPTransport,
):
    logger.info(f"Resolving {idx + 1}/{len(top_packages)} {project}")
    pending.append(project)
    try:
        start = time.time()
        cache = Cache(default_cache_dir, refresh_versions=False)
        requirement = Requirement(project)
        resolution = await resolve_requirement(
            # TODO: Force latest version
            requirement,
            requires_python,
            cache,
            transport=transport,
        )
        end = time.time()
        logging.info(f"{project} {end - start: .3f}s ({len(resolution.package_data)})")
        # logger.info(freeze(resolution, requirement))
    except (RuntimeError, ValueError, HTTPError, BuildBackendException) as e:
        failures.append(project)
        tqdm.write(str(e))
    pending.remove(project)


def single_sync(idx, project, failures, requires_python, top_packages):
    transport = AsyncHTTPTransport(retries=3)
    asyncio.run(
        single(failures, idx, project, requires_python, top_packages, transport)
    )


def main():
    pypi_top_json = root.joinpath("download").joinpath(
        "top-pypi-packages-30-days.min.json"
    )
    top_packages = [i["project"] for i in json.loads(pypi_top_json.read_text())["rows"]]
    top_packages = top_packages
    if len(sys.argv) > 1:
        top_packages = top_packages[: int(sys.argv[1])]
    requires_python = VersionSpecifiers(">=3.8,<3.12")
    # transport = AsyncHTTPTransport(retries=3)
    failures = []

    with ThreadPoolExecutor() as executor:
        bar = tqdm(
            executor.map(
                functools.partial(
                    single_sync,
                    failures=failures,
                    requires_python=requires_python,
                    top_packages=top_packages,
                ),
                *zip(*list(enumerate(top_packages)), strict=True),
            ),
            total=len(top_packages),
        )
        for _ in bar:
            bar.set_description(", ".join(pending)[:40])

    # all_start = time.time()
    # for idx, project in enumerate(tqdm(top_packages, file=sys.stdout)):
    #    asyncio.run(
    #        single(failures, idx, project, requires_python, top_packages, transport)
    #    )
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
    # all_stop = time.time()
    # print(f"{all_stop-all_start}s")
    print(f"{len(failures)}: {failures}")


if __name__ == "__main__":
    main()
