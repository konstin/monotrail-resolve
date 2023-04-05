"""You can imagine the following as a graph walking procedure: For each node (package)
we vist, we compute the outgoing edges (requires_dist) from incoming edge information
(version constraints and activated extras). The root is the user input. Each time we
give a new or change an incoming edge by this procedure we mark the node for revisiting
(breadth-first search). Given that we sometimes have to hold and wait for the network or
worse build a sdist to know the outgoing edges of a new output translation for nodes
we collect as many of those cases as possible and then query them in parallel.

We have three kinds of information we query:
* The versions (and files) that exist for each release: One query per release (fast)
* The metadata for a specific version: One query per version (fast)
* The metadata for a sdist: Download, unpack, install build requires and build by
  running potentially arbitrary code (slow)

The procedure is the following:
For each package, we store the requirements (outgoing edges) and their source (incoming
edges).
Whenever we add a requirement from A to B, we add B to the queue to apply the
requirement later.
For each package in the queue
* we check if we have the list of releases. if not, add to version fetch queue and delay
* we compute a candidate (version + extras) based on all requirements. if there is a
  conflict (set of available versions is empty), add to conflict queue
* we check if we have the metadata (requires_dist) for the candidate. if not, add to
  metadata fetch queue and delay
* we diff with the previous candidate, update the requirements store and add packages
  with changed incoming requirements (this packages new outgoing edges) to the queue
* if we see a sdist, we first pretend we didn't and resolve_prototype it as a packages
  with no deps on its own
When the queue is empty:
* If there are versions and/or metadata to be fetched, do so and mark all affected
  packages
When the queue is still empty:
* If there is wheel metadata to be fetched, fetch wheel metadata and mark all packages
  affected by removed or added requirements
When the queue is still empty:
* fetch, unpack and PEP 517 query metadata for sdists in parallel and mark all packages
  affected by new requirements
"""

import asyncio
import logging
import re
import sys
import time
from argparse import ArgumentParser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, Executor
from dataclasses import dataclass
from typing import List, Dict, Tuple, Set, Type, Iterable, Union
from typing import Optional

import httpx
import tomli_w
from httpx import AsyncClient, AsyncBaseTransport

from pypi_types import pypi_releases, pypi_metadata, core_metadata
from pypi_types.pep440_rs import Version, VersionSpecifier
from pypi_types.pep508_rs import Pep508Error, Requirement, MarkerEnvironment
from resolve_prototype.common import (
    default_cache_dir,
    Cache,
    MINIMUM_SUPPORTED_PYTHON_MINOR,
    NormalizedName,
    normalize,
)
from resolve_prototype.compare.common import resolutions_ours
from resolve_prototype.package_index import (
    get_releases,
    get_metadata,
    get_metadata_from_wheel,
)
from resolve_prototype.sdist import build_sdist

logger = logging.getLogger(__name__)


def parse_requirement_fixup(
    requirement: str, debug_source: Optional[str]
) -> Requirement:
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
            if debug_source:
                logger.warning(
                    f"Requirement `{requirement}` for {debug_source} is invalid"
                    " (missing comma)"
                )
            return requirement_parsed
        except Pep508Error:
            pass
        # Didn't work with the fixup either? raise the error with the original string
        raise


class State:
    root_requirement: Requirement
    user_constraints: Dict[NormalizedName, List[Requirement]]

    # The list of packages which we need to reevaluate
    queue: List[NormalizedName]
    # Process after fetching additional information
    fetch_versions: Set[NormalizedName]
    # The idea of a dict is that we can query a version to fetch, but if something
    # further back in the queue requires a different version constraint it gets updated
    # before fetching the now useless version
    fetch_metadata: Dict[NormalizedName, Version]
    # remember which sdist we did already process
    resolved_sdists: Set[Tuple[NormalizedName, Version]]

    # package name -> list of versions and the files (sdist and wheel only) from pypi
    versions_cache: Dict[NormalizedName, Dict[Version, List[pypi_releases.File]]]
    # The requirements for specific package version, either from wheel_metadata, or if
    # that isn't available (yet),from pypi_metadata. Extra fields because there can be
    # parsing errors with the pypi metadata
    metadata_requirements: Dict[Tuple[NormalizedName, Version], List[Requirement]]
    # (package name, package version) -> pypi metadata, possibly wrong given
    # wheel_metadata
    pypi_metadata: Dict[Tuple[NormalizedName, Version], pypi_metadata.Metadata]
    # (package name, package version) -> metadata from a while from pypi
    wheel_metadata: Dict[Tuple[NormalizedName, Version], core_metadata.Metadata21]
    # (wheel filename) -> METADATA contents
    wheel_file_metadata: Dict[str, core_metadata.Metadata21]
    # Reprocess this even if the version stayed the same
    # Stores a list of the old requirements so we can remove them
    changed_metadata: Dict[Tuple[NormalizedName, Version], List[Requirement]]
    # Reverse mapping: package name -> requirements. Without version since those are
    # the ones we determine the version from
    requirements_per_package: Dict[
        NormalizedName, Set[Tuple[Requirement, Tuple[NormalizedName, Version]]]
    ]
    # name -> (version, extras)
    candidates: Dict[NormalizedName, Tuple[Version, Set[str]]]

    # Currently used to switch out the ThreadPoolExecutor we normally use with the lazy
    # zip for a DummyExecutor
    executor: Type[Executor]

    def __init__(self, root_requirement: Requirement, executor: Type[Executor]):
        self.root_requirement = root_requirement
        self.user_constraints = {normalize(root_requirement.name): [root_requirement]}
        self.queue = [normalize(root_requirement.name)]
        self.fetch_versions = set()
        self.fetch_metadata = {}
        self.resolved_sdists = set()
        self.versions_cache = {}
        self.metadata_requirements = {}
        self.pypi_metadata = {}
        self.wheel_metadata = {}
        self.wheel_file_metadata = {}
        self.changed_metadata = {}
        self.requirements_per_package = defaultdict(set)
        self.candidates = {}
        self.executor = executor

        for name, [requirement] in self.user_constraints.items():
            # somehow it doesn't get the list unstructuring
            # noinspection PyTypeChecker
            self.requirements_per_package[name] = {
                (requirement, ("(user specified)", Version("0")))
            }

    @staticmethod
    def assert_list_normalization(data: Iterable[Union[str, NormalizedName]]):
        for entry in data:
            assert entry == normalize(entry), entry

    def assert_normalization(self):
        """:/"""
        self.assert_list_normalization(self.user_constraints.keys())
        self.assert_list_normalization(self.queue)
        self.assert_list_normalization(self.fetch_versions)
        self.assert_list_normalization(self.fetch_metadata.keys())
        self.assert_list_normalization([i[0] for i in self.resolved_sdists])
        self.assert_list_normalization([i[0] for i in self.versions_cache])
        self.assert_list_normalization([i[0] for i in self.versions_cache])
        self.assert_list_normalization(
            [i[0] for i in self.metadata_requirements.keys()]
        )
        self.assert_list_normalization([i[0] for i in self.pypi_metadata.keys()])
        self.assert_list_normalization([i[0] for i in self.wheel_metadata.keys()])
        self.assert_list_normalization([i[0] for i in self.changed_metadata.keys()])
        self.assert_list_normalization(self.requirements_per_package.keys())
        self.assert_list_normalization(self.candidates.keys())


@dataclass
class ReleaseData:
    # We use this when e.g. comparing with pip. I'm not entirely sure yet what this
    # name is and who defines it but it's easier to already track something here.
    # E.g. "Django" vs. "django
    unnormalized_name: str
    # The requirements read from the wheel or the PEP 517 api with fixups
    requirements: List[Requirement]
    # Metadata read from the wheel
    metadata: core_metadata.Metadata21
    # The list of files for this release
    files: List[pypi_releases.File]
    # The list of all extras in our resolution, which is a non-strict subset of all
    # extras of this package
    extras: Set[str]


@dataclass
class Resolution:
    # The requirements given by the user
    root: List[Requirement]
    package_data: Dict[Tuple[NormalizedName, Version], ReleaseData]

    def for_environment(
        self, env: MarkerEnvironment, root_extras: List[str]
    ) -> "Resolution":
        """Filters down the resolution to the list of packages that need to be installed
        for the given environment.

        We can assume the resolved dependencies to be a connected graph where
        packages (resolved to one version each) are nodes and outgoing edges are the
        requirements of each package. We now implement a breadth first search to
        determine the subgraph if we remove all edges that do not match the current env
        markers. This is an iterative procedure where each incoming edge comes with a
        set of extras that may change the set of outgoing edges of a node."""
        name_to_version = {name: (name, version) for name, version in self.package_data}
        # We have starting incoming edges for all root requirements
        env_root = list(
            filter(lambda req: req.evaluate_markers(env, root_extras), self.root)
        )
        selected: Set[NormalizedName] = {normalize(req.name) for req in env_root}
        # name -> extras
        # selected_extras contains only normalized keys
        selected_extras: Dict[NormalizedName, Set[str]] = defaultdict(set)
        for req in self.root:
            selected_extras[normalize(req.name)].update(req.extras or [])

        already_warned = []

        # queue contains only normalized names
        # TODO(konstin): Use a wrapper type around names so we can only compare/index
        #   with the correct normalization
        queue = [normalize(req.name) for req in env_root]
        while queue:
            current = queue.pop()
            for req in self.package_data[name_to_version[current]].requirements:
                (matches, warnings) = req.evaluate_markers_and_report(
                    env, sorted(selected_extras[current])
                )
                for warning in warnings:
                    if (current, req, warning) in already_warned:
                        continue
                    already_warned.append((current, req, warning))
                    # TODO: Collect those warnings during dependency resolution, but
                    #   warn only if the version was picked. If so, check the latest
                    #   version. If it is also invalid, prompt the user with the
                    #   bug tracker url, repository url or another url. If not, prompt
                    #   user to upgrade their deps
                    logger.warning(
                        f"Package {current} has requirement `{req}` "
                        f"with invalid marker expression `{warning[2]}`: "
                        f"{warning[1]}"
                    )
                if not matches:
                    # Skip edges that are not relevant to the current env. Note that it
                    # can also be the env markers would fit but we lack the extra
                    # because the markers did not apply to an edge closer to the root
                    # which in turn did not activate the extra
                    continue
                add_to_queue = False
                if normalize(req.name) not in selected:
                    selected.add(normalize(req.name))
                    add_to_queue = True
                if not set(req.extras or []) <= selected_extras[normalize(req.name)]:
                    selected_extras[normalize(req.name)].update(req.extras)
                    add_to_queue = True
                if add_to_queue:
                    if req.name not in queue:
                        queue.append(normalize(req.name))

        env_package_data = {}
        for (name, version), package_data in self.package_data.items():
            if name in selected:
                env_package_data[(name, version)] = package_data

        return Resolution(root=env_root, package_data=env_package_data)


def query_wheel_metadata(state: State, cache: Cache):
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
    query_wheels: List[Tuple[str, Version, str, str, Cache]] = []
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
        metadata_filename = cache.get_filename(
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
            metadatas = executor.map(get_metadata_from_wheel, *zip(*query_wheels))
    # (name, version) -> list[(url, metadata)]
    by_candidate: Dict[
        Tuple[NormalizedName, Version], List[Tuple[str, core_metadata.Metadata21]]
    ] = defaultdict(list)
    for metadata, (name, version, _filename, url, _cache) in zip(
        metadatas, query_wheels
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


async def update_single_package(
    state: State,
    name: NormalizedName,
    maximum_versions: bool,
    python_versions: List[Version],
):
    """Processes a single package, normally resolving it into a version
    Steps:
     * Check that we have the versions of the package (or delay)
     * Pick a compatible version (or error - no backtracking yet)
     * Check that we have the metadata for the version (or delay)
     * Update state.requirements_per_package
     * Queue all packages affected by changes
    """
    # We've had this branch before but the version data isn't there yet
    if name in state.fetch_versions:
        return
    logger.debug(f"Processing {name}")
    # First time we're encountering this package?
    if name not in state.versions_cache:
        logger.debug(f"Missing versions for {name}, delaying")
        state.fetch_versions.add(name)
        return
    # Apply all requirements and find the highest (given `maximum_versions`)
    # possible version
    new_version = None
    new_extras: Set[str] = set()
    for version in sorted(state.versions_cache[name].keys(), reverse=maximum_versions):
        # TODO: proper prerelease handling (i.e. check the specifiers if they
        #  have consensus over pulling specific prerelease ranges in)
        if version.any_prerelease():
            continue
        is_compatible = True
        extras: Set[str] = set()

        print(name, version, state.requirements_per_package[name])
        for requirement, _source in state.requirements_per_package[name]:
            extras.update(set(requirement.extras or []))
            if not requirement.version_or_url:
                continue
            for specifier in requirement.version_or_url:
                if not specifier.contains(version):
                    is_compatible = False
                    break
        if is_compatible:
            new_version = version
            new_extras = extras
            break
    # TODO: Actually backtrack (pubgrub?)
    if not new_version:
        raise RuntimeError(
            f"No compatible version for {name}.\n"
            f"Constraints: {state.requirements_per_package[name]}.\n"
            f"Versions: {sorted(state.versions_cache[name].keys())}"
        )
    # If we had the same constraints
    if (name, new_version) in state.changed_metadata:
        changed_requirement = state.changed_metadata.pop((name, new_version))
        logger.debug(f"New wheel metadata for {name} {new_version}")
    else:
        changed_requirement = None
    old_version, old_extras = state.candidates.get(name, (None, None))
    if new_version == old_version and new_extras == old_extras:
        if changed_requirement is None:
            logger.info(f"No changes for {name}")
            return
    else:
        if old_version:
            logger.info(
                f"Picking {name} {new_version} {new_extras} over"
                f" {old_version} {old_extras}"
            )
        else:
            logger.debug(f"Picking {name} {new_version} {new_extras}")
    # Do we actually already know the requires_dist for this new candidate?
    if (name, new_version) not in state.pypi_metadata:
        logger.debug(f"Missing metadata for {name} {new_version}, delaying")
        # If we had chosen a higher version to fetch in previous iteration,
        # overwrite
        state.fetch_metadata[normalize(name)] = new_version
        return
    state.candidates[name] = (new_version, new_extras)
    # Update the outgoing edges
    if old_version:
        if changed_requirement is not None:
            old_requirements = changed_requirement
        else:
            old_requirements = state.metadata_requirements[(name, old_version)]
        for old in old_requirements:
            if not old.evaluate_extras_and_python_version(old_extras, python_versions):
                continue
            # We always need to remove all of them since the version always
            # changed
            state.requirements_per_package[normalize(old.name)].remove(
                (old, (name, old_version))
            )

            if normalize(old.name) not in state.queue:
                logger.debug(f"Queuing {normalize(old.name)}")
                state.queue.append(normalize(old.name))
    else:
        old_requirements = []
    for new in state.metadata_requirements[(name, new_version)]:
        if not new.evaluate_extras_and_python_version(new_extras, python_versions):
            continue
        state.requirements_per_package[normalize(new.name)].add(
            (new, (name, new_version))
        )
        # Same requirement might be in two version of a package, otherwise
        # we need to recompute it
        if new not in old_requirements and normalize(new.name) not in state.queue:
            logger.debug(f"Queuing {normalize(new.name)}")
            state.queue.append(normalize(new.name))


async def build_sdists(
    state: State,
    cache: Cache,
    sdists: List[Tuple[NormalizedName, Version, pypi_releases.File]],
    transport: AsyncBaseTransport,
):
    # Download and PEP 517 query sdists for metadata
    logger.info(
        f"Building {[f'{name} {version}' for (name, version, _filename) in sdists]}"
    )
    async with AsyncClient(http2=True, transport=transport) as client:
        metadatas = await asyncio.gather(
            *[build_sdist(client, sdist[2], cache) for sdist in sdists]
        )
    for (name, version, _filename), metadata in sorted(zip(sdists, metadatas)):
        state.wheel_metadata[(name, version)] = metadata
        state.metadata_requirements[(name, version)] = metadata.requires_dist
        state.changed_metadata[(name, version)] = state.metadata_requirements[
            (name, version)
        ]
        state.resolved_sdists.add((name, version))
        if name not in state.queue:
            state.queue.append(name)

        for added in sorted(metadata.requires_dist, key=str):
            state.requirements_per_package[normalize(added.name)].add(
                (added, (name, version))
            )
            if normalize(added.name) not in state.queue:
                state.queue.append(normalize(added.name))


async def find_sdists_for_build(
    state: State,
) -> List[Tuple[NormalizedName, Version, pypi_releases.File]]:
    sdists = []
    for name, (version, _extras) in state.candidates.items():
        if (name, version) in state.resolved_sdists:
            continue
        if not any(
            file.filename.endswith(".whl")
            for file in state.versions_cache[name][version]
        ):
            try:
                [sdist] = state.versions_cache[name][version]
            except ValueError:
                sdist_list = [
                    file.filename for file in state.versions_cache[name][version]
                ]
                raise RuntimeError(
                    f"Expected exactly one sdist, found {sdist_list}"
                ) from None
            sdists.append((name, version, sdist))
    return sdists


async def fetch_versions_and_metadata(
    state: State, cache: Cache, transport: AsyncBaseTransport
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
        dict(zip(sorted(state.fetch_versions), projects_releases))
    )
    # we got the info where we delayed previously, now actually compute a candidate
    # version
    state.queue.extend(state.fetch_versions)
    state.fetch_versions.clear()

    for (name, version), metadata in zip(
        state.fetch_metadata.items(), projects_metadata
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


async def resolve(
    root_requirement: Requirement,
    requires_python: VersionSpecifier,
    cache: Cache,
    download_wheels: bool = True,
    maximum_versions: bool = True,
    executor: Type[Executor] = ThreadPoolExecutor,
) -> Resolution:
    transport = httpx.AsyncHTTPTransport(retries=3)

    # Generate list of compatible python versions for shrinking down the list of
    # dependencies. This is done to avoid implementing PEP 440 version specifier
    # intersections on both left hand and right hand between `requires_python` and the
    # markers
    python_versions = []
    for minor in range(MINIMUM_SUPPORTED_PYTHON_MINOR, 101):
        version = Version(f"3.{minor}")
        if version in requires_python:
            python_versions.append(version)
    if Version("4.0") in requires_python:
        python_versions.append(Version("4.0"))

    state = State(root_requirement, executor)

    start = time.time()

    while True:
        # We have packages for which we need to recompute the candidate
        while state.queue:
            # state.assert_normalization()
            name = state.queue.pop(0)
            await update_single_package(state, name, maximum_versions, python_versions)

        # Log the current set of candidates
        candidates_fmt = " ".join(
            [
                f"{name}{'[' + ','.join(extras) + ']' if extras else ''}=={version}"
                for name, (version, extras) in sorted(state.candidates.items())
            ]
        )
        logger.info(f"Candidates: {candidates_fmt}")

        # With have likely delay some packages because we're lacking the metadata,
        # but we want to fetch all metadata for each category at once.
        # This is the fastest to fetch metadata because we're just getting JSON
        # from a CDN
        if state.fetch_versions or state.fetch_metadata:
            await fetch_versions_and_metadata(state, cache, transport)

        # Compute candidates again first before we do the slow METADATA and sdist part,
        # maybe we get better candidates already
        if state.queue:
            # Make the resolution deterministic and easier to understand from the logs
            state.queue.sort()
            continue

        # Allow to skip this step
        if download_wheels:
            query_wheel_metadata(state, cache)

        # We found some METADSTA for missing requires_dist, we can resolve further
        # before building sdists
        if state.queue:
            state.queue.sort()
            continue

        # Everything else is resolved, time for the slowest part:
        # Do we have sdist for which we don't know the metadata yet?
        sdists = await find_sdists_for_build(state)
        if sdists:
            await build_sdists(state, cache, sdists, transport)
            continue

        # This is when we know we're done, everything is resolved and all metadata
        # is the best it can be
        break

    end = time.time()
    print(f"resolution ours took {end - start:.3f}s")

    package_data = {}
    for name, (version, _extras) in sorted(state.candidates.items()):
        package_data[(name, version)] = ReleaseData(
            unnormalized_name=state.pypi_metadata[(name, version)].name,
            requirements=state.metadata_requirements[(name, version)],
            # Currently, we only use the wheel metadata if the pypi requires_dist was
            # empty
            metadata=state.wheel_metadata.get((name, version))
            or state.pypi_metadata[(name, version)],
            files=state.versions_cache[name][version],
            extras=state.candidates[name][1],
        )

    return Resolution([root_requirement], package_data)


def freeze(resolution: Resolution, root_requirement: Requirement) -> str:
    """Write out in the same format as `pip freeze`"""
    resolutions_ours.mkdir(exist_ok=True)
    assert "/" not in str(root_requirement)

    # We want to have a trailing newline
    lines = [
        f"{package_data.unnormalized_name}=={version}\n"
        for (name, version), package_data in resolution.package_data.items()
    ]
    resolutions_ours.joinpath(str(root_requirement)).with_suffix(".txt").write_text(
        "".join(lines)
    )
    toml_data = {}
    for (name, version), package_data in sorted(resolution.package_data.items()):
        toml_data[name] = {
            "version": str(version),
            "requirements": [str(req) for req in package_data.requirements],
        }

    pseudo_lock_file = resolutions_ours.joinpath(str(root_requirement)).with_suffix(
        ".toml"
    )
    # much faster than tomlkit
    pseudo_lock_file.write_text(tomli_w.dumps(toml_data))
    return "".join(lines)


def main():
    logging.basicConfig(level=logging.INFO)
    logging.captureWarnings(True)

    parser = ArgumentParser()
    parser.add_argument("--refresh-versions", action="store_true")
    parser.add_argument("--requires-python", default=">= 3.7")
    parser.add_argument("requirement")
    args = parser.parse_args()

    root_requirement = Requirement(args.requirement)
    # root_requirement = Requirement("meine_stadt_transparent")
    # root_requirement = Requirement(
    #    "transformers[torch,sentencepiece,tokenizers,torch-speech,vision,"
    #    "integrations,timm,torch-vision,codecarbon,accelerate,video]"
    # )
    # root_requirement = Requirement("ibis-framework[all]")
    # root_requirement = Requirement("bio_embeddings[all]")

    requires_python = VersionSpecifier(args.requires_python)

    if len(sys.argv) == 2:
        root_requirement = Requirement(sys.argv[1])
    start = time.time()
    resolution: Resolution = asyncio.run(
        resolve(
            root_requirement,
            requires_python,
            Cache(default_cache_dir, refresh_versions=args.refresh_versions),
        )
    )
    print(freeze(resolution, root_requirement))
    end = time.time()
    print(f"Took {end - start:.2f}s")


if __name__ == "__main__":
    main()
