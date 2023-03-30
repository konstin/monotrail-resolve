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
* If there are versions and/or metadata to be fetched, do so. queue all those for
  which we now have new metadata
When the queue is still empty:
* fetch, unpack and PEP 517 query metadata for sdists in parallel, invalid them with
  `changed_metadata` and queue a
"""

import asyncio
import logging
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, Executor
from dataclasses import dataclass
from typing import List, Dict, Tuple, Set, Type
from typing import Optional

import httpx
import tomli_w
from httpx import AsyncClient

from pypi_types import pypi_releases, pypi_metadata
from pypi_types.pep440_rs import Version, VersionSpecifier
from pypi_types.pep508_rs import Pep508Error, Requirement, MarkerEnvironment
from resolve_prototype.common import (
    normalize,
    default_cache_dir,
    Cache,
    MINIMUM_SUPPORTED_PYTHON_MINOR,
    NormalizedName,
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
    versions_cache: Dict[str, Dict[Version, List[pypi_releases.File]]]
    # (package name, package version) -> Python core metadata (or at least the part
    # we currently use of it)
    metadata_cache: Dict[Tuple[str, Version], pypi_metadata.Metadata]
    # For sdists, we pick a candidates before we have the correct requires_dist, so we
    # have to force an update without candidate change when we have an update
    changed_metadata: Set[Tuple[NormalizedName, Version]]
    # We need to remove the old edges, so we need to remember the wrong metadata
    old_metadata: Dict[Tuple[NormalizedName, Version], pypi_metadata.Metadata]
    # We query wheel metadata through range requests and save it here
    wheel_metadata_cache: Dict[str, pypi_metadata.Metadata]

    requirements_per_package: Dict[
        NormalizedName, Set[Tuple[Requirement, Tuple[NormalizedName, Version]]]
    ]
    # name -> (version, extras)
    candidates: Dict[NormalizedName, Tuple[Version, Set[str]]]

    # Currently used to switch out the ThreadPoolExecutor we normally use with the lazy
    # zip for a DummyExecutor
    executor: Type[Executor]

    def __init__(self, root_requirement: Requirement, executor: Type[Executor]):
        self.old_metadata = {}
        self.root_requirement = root_requirement
        self.user_constraints = {normalize(root_requirement.name): [root_requirement]}
        self.queue = [normalize(root_requirement.name)]
        self.fetch_versions = set()
        self.fetch_metadata = {}
        self.resolved_sdists = set()
        self.versions_cache = {}
        self.metadata_cache = {}
        self.changed_metadata = set()
        self.wheel_metadata_cache = {}
        self.requirements_per_package = defaultdict(set)
        self.candidates = dict()
        self.executor = executor

        for name, [requirement] in self.user_constraints.items():
            # somehow it doesn't get the list unstructuring
            # noinspection PyTypeChecker
            self.requirements_per_package[name] = {
                (requirement, ("(user specified)", Version("0")))
            }


@dataclass
class ReleaseData:
    # We use this when e.g. comparing with pip. I'm not entirely sure yet what this
    # name is and who defines it but it's easier to already track something here.
    # E.g. "Django" vs. "django
    unnormalized_name: str
    # The requirements read from the wheel or the PEP 517 api with fixups
    requirements: List[Requirement]
    # Metadata read from the wheel or the PEP 517 api
    metadata: pypi_metadata.Metadata
    # The list of files for this release
    files: List[pypi_releases.File]


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
                if not set(req.extras or []) <= selected_extras[req.name]:
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
        while state.queue:
            name = state.queue.pop(0)
            logger.debug(f"Processing {name}")
            # First time we're encountering this package?
            if name not in state.versions_cache:
                logger.debug(f"Missing versions for {name}, delaying")
                state.fetch_versions.add(name)
                continue

            # Apply all requirements and find the highest (given `maximum_versions`)
            # possible version
            new_version = None
            new_extras = None
            for version in sorted(
                state.versions_cache[name].keys(), reverse=maximum_versions
            ):
                # TODO: proper prerelease handling (i.e. check the specifiers if they
                #  have consensus over pulling specific prerelease ranges in)
                if version.any_prerelease():
                    continue
                is_compatible = True
                extras = set()

                for requirement, _source in state.requirements_per_package[name]:
                    extras.update(requirement.extras or [])
                    if not requirement.version_or_url:
                        continue
                    for specifier in requirement.version_or_url:
                        if not specifier.contains(version):
                            is_compatible = False
                            break
                if is_compatible:
                    if metadata := state.metadata_cache.get((name, version)):
                        all_valid = True
                        for requirement in metadata.requires_dist or []:
                            try:
                                parse_requirement_fixup(
                                    requirement, f"{name} {version}"
                                )
                            except Pep508Error:
                                # Yep this even happens surprisingly often
                                logger.warning(
                                    f"Ignoring {name} {version} due to invalid"
                                    f" requires_dist entry `{requirement}`: e"
                                )
                                all_valid = False
                                break
                        if not all_valid:
                            continue
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
            old_version, old_extras = state.candidates.get(name, (None, None))
            if new_version == old_version and new_extras == old_extras:
                if (name, new_version) not in state.changed_metadata:
                    logger.info(f"No changes for {name}")
                    continue
                else:
                    logger.info(f"Changed metadata for {name} {new_version}")
            else:
                if old_version:
                    logger.info(
                        f"Picking {name} {new_version} {new_extras} over"
                        f" {old_version} {old_extras}"
                    )
                else:
                    logger.debug(f"Picking {name} {new_version} {new_extras}")

            # Do we actually already know the requires_dist for this new candidate?
            if (name, new_version) not in state.metadata_cache:
                logger.debug(f"Missing metadata for {name} {new_version}, delaying")
                # If we had chosen a higher version to fetch in previous iteration,
                # overwrite
                state.fetch_metadata[name] = new_version
                continue

            # Update the outgoing edges
            if old_version:
                old_requires_dist = (
                    state.metadata_cache[(name, old_version)].requires_dist or []
                )
                old_requirements = set()
                for requires_dist in old_requires_dist:
                    requirement = parse_requirement_fixup(requires_dist, None)
                    if requirement.evaluate_extras_and_python_version(
                        old_extras, python_versions
                    ):
                        old_requirements.add(requirement)

            else:
                old_requirements = set()

            # Edge case: The old metadata on pypi was a lie, we have updated the
            # metadata from wheel metadata overwrite what we had normally done in the
            # last step
            if (name, new_version) in state.changed_metadata:
                for _, value in state.requirements_per_package.items():
                    reqs = list(filter(lambda x: x[1] == (name, new_version), value))
                    # multiple requirements for the same package are not forbidden
                    # (might even make sense with markers)
                    for req in reqs:
                        value.remove(req)
                old_requirements = set()

            new_requires_dist = (
                state.metadata_cache[(name, new_version)].requires_dist or []
            )
            new_requirements = set()
            for requires_dist in new_requires_dist:
                requirement = parse_requirement_fixup(requires_dist, None)
                if requirement.evaluate_extras_and_python_version(
                    new_extras, python_versions
                ):
                    new_requirements.add(requirement)

            state.candidates[name] = (new_version, new_extras)

            # Remove and add edges. For (old_requirements & new_requirements) we just
            # change the candidate in there
            # and the requirement stay the same
            for old in old_requirements:
                old_entry = (old, (name, old_version))
                # For sdist we didn't add anything the first time because we didn't know
                # requires_dist yet, so we can't remove that now
                if (
                    (name, new_version) in state.changed_metadata
                    and old_entry not in state.requirements_per_package[old.name]
                ):
                    continue
                # otherwise this must be there
                state.requirements_per_package[normalize(old.name)].remove(old_entry)
            for new in new_requirements:
                state.requirements_per_package[normalize(new.name)].add(
                    (new, (name, new_version))
                )
            # Queue the packages with actually changed requirements for recalculation
            for changed in (old_requirements | new_requirements) - (
                old_requirements & new_requirements
            ):
                # For fetch_versions it's no use to requeue this here (we still don't
                # know which version do even exist), but for fetch_metadata we might
                # pick a different version in the next iteration and avoid fetching
                # useless metadata
                if (
                    changed.name not in state.queue
                    and changed.name not in state.fetch_versions
                ):
                    logger.debug(f"Queuing {changed.name}")
                    state.queue.append(normalize(changed.name))

            if (name, new_version) in state.changed_metadata:
                state.changed_metadata.remove((name, new_version))

        candidates_fmt = " ".join(
            [
                f"{name}{'[' + ','.join(extras) + ']' if extras else ''}=={version}"
                for name, (version, extras) in sorted(state.candidates.items())
            ]
        )
        logger.info(f"Candidates: {candidates_fmt}")
        if state.fetch_versions or state.fetch_metadata:
            logger.info(
                f"Fetching versions for {len(state.fetch_versions)} and also"
                f" metadata for {len(state.fetch_metadata)}"
            )

        async with AsyncClient(http2=True, transport=transport) as client:
            projects_releases = await asyncio.gather(
                *[
                    get_releases(client, name, cache)
                    for name in sorted(state.fetch_versions)
                ]
            )
        state.versions_cache.update(
            dict(zip(sorted(state.fetch_versions), projects_releases))
        )
        # we got the info where we delayed previously, now actually compute a candidate
        # version
        state.queue.extend(state.fetch_versions)
        state.fetch_versions.clear()

        # noinspection PyTypeChecker
        fetch_metadata_sorted: List[Tuple[str, Version]] = sorted(
            state.fetch_metadata.items()
        )
        async with AsyncClient(http2=True, transport=transport) as client:
            projects_metadata = await asyncio.gather(
                *[
                    get_metadata(client, name, version, cache)
                    for name, version in fetch_metadata_sorted
                ]
            )
        state.metadata_cache.update(dict(zip(fetch_metadata_sorted, projects_metadata)))
        # we got the info where we delayed previously, now actually propagate those
        # requirements
        state.queue.extend(state.fetch_metadata)
        state.fetch_metadata.clear()

        # Make the resolution deterministic and easier to understand from the logs
        state.queue.sort()
        # Do everything else first before we do the slow sdist part
        if state.queue:
            continue

        # Check the packages with wheels with empty requires_dist, they might not be so
        # empty after all (name. version, filename, url)
        query_wheels: List[Tuple[str, Version, str, Cache]] = []
        for name, (version, _extras) in state.candidates.items():
            # Here we only want to check for those where requires_dist is empty
            if state.metadata_cache[(name, version)].requires_dist:
                continue

            for file in state.versions_cache[name][version]:
                if (
                    file.filename.endswith(".whl")
                    and file.filename not in state.wheel_metadata_cache
                ):
                    # TODO: Make sure it's an all-or-nothing per release here
                    query_wheels.append((name, version, file.url, cache))
                    break

        # Allow to skip this step
        if not download_wheels:
            query_wheels = []

        # Actually download the wheel metadata from the exact section of the zip
        if query_wheels:
            logger.info(
                f"Validating wheel metadata for {len(query_wheels)} empty requires_dist"
            )
            # ZipFile doesn't support async :/
            with ThreadPoolExecutor() as executor:
                metadatas = executor.map(get_metadata_from_wheel, *zip(*query_wheels))
            by_candidate: Dict[
                Tuple[NormalizedName, Version], List[Tuple[str, pypi_metadata.Metadata]]
            ] = defaultdict(list)
            for metadata, (name, version, url, _) in zip(metadatas, query_wheels):
                by_candidate[(name, version)].append((url, metadata))
            for (name, version), metadatas in by_candidate.items():
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
                state.wheel_metadata_cache[metadatas[0][0]] = metadata
                if (
                    state.metadata_cache[(name, version)].requires_dist
                    != metadata.requires_dist
                ):
                    logger.warning(
                        f"Diverging requires_dist metadata for {name} {version}:\n"
                        f"pypi json api: "
                        f"{state.metadata_cache[(name, version)].requires_dist or []}\n"
                        f"wheel metadata: {metadata.requires_dist}"
                    )
                    state.old_metadata[(name, version)] = state.metadata_cache[
                        (name, version)
                    ]
                    state.metadata_cache[(name, version)] = metadata
                    state.changed_metadata.add((name, version))
                    state.queue.append(name)

        # We found some missing requires_dist, we can resolve further before building
        # sdists
        if state.queue:
            continue

        # Do we have sdist for which we don't know the metadata yet?
        sdists: List[Tuple[str, Version, pypi_releases.File]] = []
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

        # This is when we know we're done, everything is resolved
        if not sdists:
            break

        # Download and PEP 517 query sdists for metadata
        logger.info(
            f"Building {[f'{name} {version}' for (name, version, _filename) in sdists]}"
        )
        async with AsyncClient(http2=True, transport=transport) as client:
            metadatas = await asyncio.gather(
                *[build_sdist(client, sdist[2], cache) for sdist in sdists]
            )
        for (name, version, _filename), metadata in zip(sdists, metadatas):
            state.metadata_cache[(name, version)] = metadata
            state.resolved_sdists.add((name, version))
            state.queue.append(name)
            state.changed_metadata.add((name, version))

    end = time.time()
    print(f"resolution ours took {end - start:.3f}s")

    package_data = {}
    for name, (version, _extras) in sorted(state.candidates.items()):
        package_data[(name, version)] = ReleaseData(
            unnormalized_name=state.metadata_cache[(name, version)].name,
            requirements=[
                parse_requirement_fixup(requires_dist, None)
                for requires_dist in (
                    state.metadata_cache[(name, version)].requires_dist or []
                )
            ],
            metadata=state.metadata_cache[(name, version)],
            files=state.versions_cache[name][version],
        )

    return Resolution([root_requirement], package_data)


def freeze(resolution: Resolution, root_requirement: Requirement) -> str:
    """Write out in the same format as `pip freeze`"""
    resolutions_ours.mkdir(exist_ok=True)

    # We want to have a trailing newline
    lines = [
        f"{package_data.unnormalized_name}=={version}\n"
        for (name, version), package_data in resolution.package_data.items()
    ]
    resolutions_ours.joinpath(root_requirement.name).with_suffix(".txt").write_text(
        "".join(lines)
    )
    toml_data = {}
    for (name, version), package_data in sorted(resolution.package_data.items()):
        toml_data[name] = {
            "version": str(version),
            "requirements": [str(req) for req in package_data.requirements],
        }

    pseudo_lock_file = resolutions_ours.joinpath(root_requirement.name).with_suffix(
        ".toml"
    )
    # much faster than tomlkit
    pseudo_lock_file.write_text(tomli_w.dumps(toml_data))
    return "".join(lines)


def main():
    logging.basicConfig(level=logging.INFO)
    logging.captureWarnings(True)

    root_requirement = Requirement("black[d,jupyter]")
    # root_requirement = Requirement("meine_stadt_transparent")
    # root_requirement = Requirement(
    #    "transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]"
    # )
    # root_requirement = Requirement("ibis-framework[all]")
    # root_requirement = Requirement("bio_embeddings[all]")

    requires_python = VersionSpecifier(">= 3.7")

    if len(sys.argv) == 2:
        root_requirement = Requirement(sys.argv[1])
    start = time.time()
    resolution: Resolution = asyncio.run(
        resolve(root_requirement, requires_python, Cache(default_cache_dir))
    )
    print(freeze(resolution, root_requirement))
    end = time.time()
    print(f"Took {end - start:.2}s")


if __name__ == "__main__":
    main()
