import asyncio
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx
from httpx import AsyncBaseTransport, AsyncClient

from pypi_types import core_metadata
from pypi_types.pep440_rs import Version
from pypi_types.pep508_rs import Pep508Error, Requirement
from resolve_prototype import Cache
from resolve_prototype.common import NormalizedName, normalize
from resolve_prototype.package_index import (
    get_metadata_from_wheel,
    get_releases,
    get_metadata,
)

if TYPE_CHECKING:
    from resolve_prototype.resolve import State

logger = logging.getLogger(__name__)


async def query_wheel_metadata(state: "State", cache: Cache):
    """Actually download the wheel metadata from the exact section of the zip.

    Here we only want to check for those where requires_dist is empty. That is because
    e.g. https://files.pythonhosted.org/packages/9f/cd/670e5e178db87065ee60f60fb35b040abbb819a1f686a91d9ff799fc5048/torch-2.0.0-1-cp310-cp310-manylinux2014_aarch64.whl
    has only the metadata for aarch64 and misses the conditional for x64:
    Diverging requires_dist metadata for torch 2.0.0:
    pypi json api: ["filelock", "typing-extensions", "sympy", "networkx", "jinja2",
    "nvidia-cuda-nvrtc-cu11 == 11.7.99 ; platform_system == 'Linux' and
    platform_machine == 'x86_64'", ...]
    wheel metadata (https://files.pythonhosted.org/packages/9f/cd/670e5e178db87065ee60f60fb35b040abbb819a1f686a91d9ff799fc5048/torch-2.0.0-1-cp310-cp310-manylinux2014_aarch64.whl):
    ["filelock", "typing-extensions", "sympy", "networkx", "jinja2",
    "opt-einsum >= 3.3; extra == 'opt-einsum'"]
    """
    # Check the packages with wheels with empty requires_dist, they might not be so
    # empty after all (name, version, filename, url, cache)
    query_wheels: list[tuple[str, Version, str, str, Cache]] = []
    for name, (version, _extras) in state.candidates.items():
        # See doc comment
        if state.pypi_metadata[(name, version)].requires_dist:
            continue

        for file in state.versions_cache[name][version]:
            if file.filename.endswith(".whl"):
                if file.url not in state.wheel_file_metadata:
                    query_wheels.append((name, version, file.filename, file.url, cache))
                # TODO(konstin): Make sure it's an all-or-nothing per release here
                break

    logger.info(f"Validating wheel metadata for {len(query_wheels)} packages")

    # Spawning a thread pool is expensive, only do it if we need it
    all_cached = True
    for _name, _version, filename, _url, cache in query_wheels:
        metadata_filename = cache.get_path(
            "wheel_metadata", f"{filename.split('/')[0]}.metadata"
        )
        if not metadata_filename.is_file():
            all_cached = False
            break

    if all_cached:
        logger.debug("get_metadata_from_wheel without ThreadPoolExecutor (all cached)")
        metadatas = [get_metadata_from_wheel(*x) for x in query_wheels]
    else:
        logger.debug("get_metadata_from_wheel with ThreadPoolExecutor (not all cached)")
        # ZipFile doesn't support async :/
        with ThreadPoolExecutor() as executor:
            metadatas = executor.map(
                get_metadata_from_wheel, *zip(*query_wheels, strict=True)
            )
    # (name, version) -> list[(url, metadata)]
    by_candidate: dict[
        tuple[NormalizedName, Version], list[tuple[str, core_metadata.Metadata21]]
    ] = defaultdict(list)
    for metadata, (name, version, _filename, url, _cache) in zip(
        metadatas, query_wheels, strict=True
    ):
        if isinstance(metadata, Exception):
            logger.warning(
                f"Failed to parse METADATA for {name} {version} in {url}, "
                f"removing it from the selection: {metadata}"
            )
            state.versions_cache[name].pop(version)
            state.queue.append(name)
        else:
            by_candidate[(name, version)].append((url, metadata))
    for (name, version), metadatas in by_candidate.items():
        # TODO: actually check all wheels per release here
        metadata = metadatas[0][1]
        for url, other_metadata in metadatas:
            assert metadata == other_metadata, (
                name,
                version,
                metadatas[0][0],
                metadata,
                url,
                other_metadata,
            )
        state.wheel_file_metadata[metadatas[0][0]] = metadata
        state.wheel_metadata[(name, version)] = metadata
        state.changed_metadata[(name, version)] = state.metadata_requirements[
            (name, version)
        ]
        state.metadata_requirements[(name, version)] = metadata.requires_dist
        pypi_requirements = [
            parse_requirement_fixup(requirement, f"{name} {version}")
            for requirement in state.pypi_metadata[(name, version)].requires_dist or []
        ]
        if pypi_requirements != metadata.requires_dist:
            if not pypi_requirements:
                logger.debug(
                    f"Missing requires_dist pypi metadata for {name} {version}"
                )
            else:
                logger.warning(
                    f"Diverging requires_dist metadata for {name} {version}:\n"
                    f"pypi json api: {pypi_requirements}\n"
                    f"wheel metadata: {metadata.requires_dist}"
                )
            for removed in set(pypi_requirements) - set(metadata.requires_dist):
                state.requirements_per_package[normalize(removed.name)].remove(
                    (removed, (name, version))
                )
                if name not in state.queue:
                    state.queue.append(name)
            for added in set(pypi_requirements) - set(metadata.requires_dist):
                state.requirements_per_package[normalize(added.name)].remove(
                    (added, (name, version))
                )
                if name not in state.queue:
                    state.queue.append(name)
            if name not in state.queue:
                state.queue.append(name)


async def fetch_versions_and_metadata(
    state: "State", cache: Cache, transport: AsyncBaseTransport
):
    logger.info(
        f"Fetching versions for {len(state.fetch_versions)} project(s) and "
        f"metadata for {len(state.fetch_metadata)} version(s)"
    )
    # noinspection PyTypeChecker
    state.fetch_metadata = dict(sorted(state.fetch_metadata.items()))
    timeout = httpx.Timeout(10.0, connect=10.0)
    async with AsyncClient(http2=True, transport=transport, timeout=timeout) as client:
        projects_releases = await asyncio.gather(
            *[
                get_releases(client, name, cache)
                for name in sorted(state.fetch_versions)
            ]
        )
        projects_metadata = await asyncio.gather(
            *[
                get_metadata(client, name, version, cache)
                for name, version in state.fetch_metadata.items()
            ]
        )
    state.versions_cache.update(
        dict(zip(sorted(state.fetch_versions), projects_releases, strict=True))
    )
    # we got the info where we delayed previously, now actually compute a candidate
    # version
    state.queue.extend(state.fetch_versions)
    state.fetch_versions.clear()

    for (name, version), metadata in zip(
        state.fetch_metadata.items(), projects_metadata, strict=True
    ):
        try:
            state.metadata_requirements[(name, version)] = [
                parse_requirement_fixup(requirement, f"{name} {version}")
                for requirement in metadata.requires_dist or []
            ]
        except Pep508Error as err:
            logger.warning(
                f"Invalid requirements for {name} {version}, "
                f"skipping this release: {err}"
            )
            # Take this version out of the rotation
            state.versions_cache[name].pop(version)
            if name not in state.queue:
                state.queue.append(name)
        state.pypi_metadata[(name, version)] = metadata
    # we got the info where we delayed previously, now actually propagate those
    # requirements
    state.queue.extend(set(state.fetch_metadata) - set(state.queue))
    state.fetch_metadata.clear()


def parse_requirement_fixup(requirement: str, debug_source: str | None) -> Requirement:
    """Fix unfortunately popular errors such as `elasticsearch-dsl (>=7.2.0<8.0.0)` in
    django-elasticsearch-dsl 7.2.2 with a regex heuristic

    None is a shabby way to signal not to warn, this should be solved properly by only
    caching once and then warn
    """
    try:
        return Requirement(requirement)
    except Pep508Error:
        try:
            # Add the missing comma
            requirement_parsed = Requirement(
                re.sub(r"(\d)([<>=~^!])", r"\1,\2", requirement)
            )

        except Pep508Error:
            pass
        else:
            if debug_source:
                logger.warning(
                    f"Requirement `{requirement}` for {debug_source} is invalid"
                    " (missing comma)"
                )
            return requirement_parsed
        # Didn't work with the fixup either? raise the error with the original string
        raise
