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
import sys
import time
from argparse import ArgumentParser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, Executor
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any

import httpx
import tomli_w

from pypi_types import pypi_releases
from pypi_types.pep440_rs import Version, VersionSpecifiers
from pypi_types.pep508_rs import Requirement, MarkerEnvironment
from resolve_prototype.common import (
    default_cache_dir,
    Cache,
    MINIMUM_SUPPORTED_PYTHON_MINOR,
    NormalizedName,
    normalize,
)
from resolve_prototype.compare.common import resolutions_ours
from resolve_prototype.metadata import (
    get_deps_for_versions,
    fetch_versions_and_metadata,
)
from resolve_prototype.package_index import get_files_for_version
from resolve_prototype.sdist import build_sdists

logger = logging.getLogger(__name__)


class State:
    root_requirement: Requirement
    user_constraints: dict[NormalizedName, list[Requirement]]

    # The list of packages which we need to reevaluate
    queue: list[NormalizedName]
    # Process after fetching additional information
    fetch_versions: set[NormalizedName]
    # The idea of a dict is that we can query a version to fetch, but if something
    # further back in the queue requires a different version constraint it gets updated
    # before fetching the now useless version
    fetch_metadata: dict[NormalizedName, Version]
    # remember which sdist we did already process
    resolved_sdists: set[tuple[NormalizedName, Version]]

    # package name -> list of versions and the files (sdist and wheel only) from pypi
    versions_cache_new: dict[NormalizedName, list[Version]]
    files_cache: dict[NormalizedName, dict[Version, list[pypi_releases.File]]]
    # The requirements for specific package version, either `requiress_dist`
    # wheel metadata, or if that isn't available (yet), from pypi metadata.
    requirements: dict[tuple[NormalizedName, Version], list[Requirement]]
    # Track separately if the metadata is from a wheel (credible) or from pypi
    # (occasionally wrong)
    requirements_credible: set[tuple[NormalizedName, Version]]
    # Reprocess this even if the version stayed the same
    # Stores a list of the old requirements so we can remove them
    changed_metadata: dict[tuple[NormalizedName, Version], list[Requirement]]
    # Reverse mapping: package name -> requirements. Without version since those are
    # the ones we determine the version from
    requirements_per_package: dict[
        NormalizedName, set[tuple[Requirement, tuple[NormalizedName, Version]]]
    ]
    # name -> (version, extras)
    candidates: dict[NormalizedName, tuple[Version, set[str]]]

    # Currently used to switch out the ThreadPoolExecutor we normally use with the lazy
    # zip for a DummyExecutor
    executor: type[Executor]

    def __init__(self, root_requirement: Requirement, executor: type[Executor]):
        self.root_requirement = root_requirement
        self.user_constraints = {normalize(root_requirement.name): [root_requirement]}
        self.queue = [normalize(root_requirement.name)]
        self.fetch_versions = set()
        self.fetch_metadata = {}
        self.resolved_sdists = set()
        # TODO: remove the _new suffix
        self.versions_cache_new = {}
        self.files_cache = {}
        self.requirements = {}
        self.requirements_credible = set()
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
    def assert_list_normalization(data: Iterable[str | NormalizedName]):
        for entry in data:
            assert entry == normalize(entry), entry


@dataclass
class ReleaseData:
    # We use this when e.g. comparing with pip. I'm not entirely sure yet what this
    # name is and who defines it but it's easier to already track something here.
    # E.g. "Django" vs. "django
    unnormalized_name: str
    # The requirements read from the wheel or the PEP 517 api with fixups
    requirements: list[Requirement]
    # The list of files for this release
    files: list[pypi_releases.File]
    # The list of all extras in our resolution, which is a non-strict subset of all
    # extras of this package
    extras: set[str]


@dataclass
class Resolution:
    # The requirements given by the user
    root: list[Requirement]
    package_data: dict[tuple[NormalizedName, Version], ReleaseData]

    def for_environment(
        self, env: MarkerEnvironment, root_extras: list[str]
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
        selected: set[NormalizedName] = {normalize(req.name) for req in env_root}
        # name -> extras
        # selected_extras contains only normalized keys
        selected_extras: dict[NormalizedName, set[str]] = defaultdict(set)
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


async def update_single_package(
    state: State,
    name: NormalizedName,
    maximum_versions: bool,
    python_versions: list[Version],
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
    if name not in state.versions_cache_new:
        logger.debug(f"Missing versions for {name}, delaying")
        state.fetch_versions.add(name)
        return
    # Apply all requirements and find the highest (given `maximum_versions`)
    # possible version
    new_version = None
    new_extras: set[str] = set()

    requirements = state.requirements_per_package[name]
    allowed_preleases = get_allowed_prereleases(requirements)
    # Only prereleases? We have to pick a prerelease, so they are all allowed
    # iirc pip added this behaviour for black. TODO: Find the issue/PR
    # The all should be fast because it should short-circuit
    if not allowed_preleases and all(
        version.any_prerelease() for version in state.versions_cache_new[name]
    ):
        allowed_preleases = set(
            tuple(version.release) for version in state.versions_cache_new[name]
        )

    for version in sorted(state.versions_cache_new[name], reverse=maximum_versions):
        # TODO: proper prerelease handling (i.e. check the specifiers if they
        #  have consensus over pulling specific prerelease ranges in)
        if version.any_prerelease() and tuple(version.release) not in allowed_preleases:
            continue
        is_compatible = True
        extras: set[str] = set()

        logger.debug(f"{name} {version} {state.requirements_per_package[name]}")
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
        constraints = state.requirements_per_package[name]
        constraints = "\n".join(
            sorted(
                f"    {req} ({requester_name} {requester_version})"
                for (req, (requester_name, requester_version)) in constraints
            )
        )
        versions = list(
            str(i).replace("'", "") for i in sorted(state.versions_cache_new[name])
        )
        raise RuntimeError(
            f"No compatible version for {name}.\n"
            f"Constraints:\n{constraints}.\n"
            f"Versions: {versions}"
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
    if (name, new_version) not in state.requirements:
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
            old_requirements = state.requirements[(name, old_version)]
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
    for new in state.requirements[(name, new_version)]:
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


def get_allowed_prereleases(
    requirements: Iterable[tuple[Requirement, Any]]
) -> set[tuple[int]]:
    # We allow prereleases for a specific stable release if all requirements have a
    # prerelease specifier for it. Since a requirement can have multiple specifier
    # which may have multiple prereleases, we use sets.
    allowed_preleases = None
    for requirement, _ in requirements:
        # Blank requirements mean prereleases are banned(?, blank requirements are bad)
        if not requirement.version_or_url:
            return set()
        # TODO: url
        # Shortcut
        if not any(
            specifier.version.any_prerelease()
            for specifier in requirement.version_or_url
        ):
            return set()
        release_with_pre = set()
        for specifier in requirement.version_or_url:
            if specifier.version.any_prerelease():
                release_with_pre.add(tuple(specifier.version.release))
        if allowed_preleases is None:
            allowed_preleases = release_with_pre
        else:
            # Only those that are allowed by all stable releases
            allowed_preleases = allowed_preleases & release_with_pre
        # Optimization
        if not allowed_preleases:
            return set()
    return allowed_preleases or {}


async def find_sdists_for_build(
    state: State, cache: Cache
) -> list[tuple[NormalizedName, Version, pypi_releases.File]]:
    sdists = []
    for name, (version, _extras) in state.candidates.items():
        if (name, version) in state.resolved_sdists:
            continue
        if version not in state.files_cache.setdefault(name, {}):
            state.files_cache[name][version] = get_files_for_version(
                cache, name, version
            )
        if not any(
            file.filename.endswith(".whl") for file in state.files_cache[name][version]
        ):
            try:
                [sdist] = state.files_cache[name][version]
            except ValueError:
                sdist_list = [
                    file.filename for file in state.files_cache[name][version]
                ]
                raise RuntimeError(
                    f"Expected exactly one sdist, found {sdist_list}"
                ) from None
            sdists.append((name, version, sdist))
    return sdists


async def resolve_requirement(
    root_requirement: Requirement,
    requires_python: VersionSpecifiers,
    cache: Cache,
    download_wheels: bool = True,
    maximum_versions: bool = True,
    executor: type[Executor] = ThreadPoolExecutor,
    transport: httpx.AsyncHTTPTransport | None = None,
) -> Resolution:
    if not transport:
        transport = httpx.AsyncHTTPTransport(retries=3)

    # Generate list of compatible python versions for shrinking down the list of
    # dependencies. This is done to avoid implementing PEP 440 version specifier
    # intersections on both left hand and right hand between `requires_python` and the
    # markers
    python_versions = []
    for minor in range(MINIMUM_SUPPORTED_PYTHON_MINOR, 101):
        version = Version(f"3.{minor}")
        if all(version in i for i in requires_python):
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

        if download_wheels:  # Allow to skip this step
            await get_deps_for_versions(state, cache)

        # We found some METADATA for missing requires_dist, we can resolve further
        # before building sdists
        if state.queue:
            state.queue.sort()
            continue

        # Everything else is resolved, time for the slowest part:
        # Do we have sdist for which we don't know the metadata yet?
        sdists = await find_sdists_for_build(state, cache)
        if sdists:
            await build_sdists(state, cache, sdists, transport)
            continue

        # This is when we know we're done, everything is resolved and all metadata
        # is the best it can be
        break

    end = time.time()
    logger.info(f"resolution ours took {end - start:.3f}s")

    package_data = {}
    for name, (version, _extras) in sorted(state.candidates.items()):
        package_data[(name, version)] = ReleaseData(
            unnormalized_name=name,  # Normalization handling
            requirements=state.requirements[(name, version)],
            files=state.files_cache[name][version],
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
    parser.add_argument("--requires-python", default=">=3.7,<3.12")
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

    requires_python = VersionSpecifiers(args.requires_python)

    if len(sys.argv) == 2:
        root_requirement = Requirement(sys.argv[1])
    start = time.time()
    resolution: Resolution = asyncio.run(
        resolve_requirement(
            root_requirement,
            requires_python,
            Cache(default_cache_dir, refresh_versions=args.refresh_versions),
        )
    )
    print(freeze(resolution, root_requirement))
    end = time.time()
    print(f"Took {end - start:.3f}s")


if __name__ == "__main__":
    main()
