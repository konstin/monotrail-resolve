import asyncio
import sys
from collections import defaultdict

import pypi_types
import tomli
import tomli_w

from pypi_types.pep440_rs import Version
from pypi_types.pep440_rs import VersionSpecifier
from pypi_types.pep508_rs import Requirement
from resolve_prototype.common import Cache, default_cache_dir, normalize
from resolve_prototype.compare.common import resolutions_ours, resolutions_poetry
from resolve_prototype.resolve import Resolution, resolve_requirement

root_requirement = Requirement("ibis-framework[all]")
minimum_python_minor = 8
requires_python = VersionSpecifier(f">=3.{minimum_python_minor}")

if len(sys.argv) == 2:
    root_requirement = Requirement(sys.argv[1])
resolution: Resolution = asyncio.run(
    resolve_requirement(
        root_requirement,
        requires_python,
        Cache(default_cache_dir, refresh_versions=False),
    )
)

python_versions = []
for minor in range(minimum_python_minor, 101):
    version = Version(f"3.{minor}")
    if version in requires_python:
        python_versions.append(version)
if Version("4.0") in requires_python:
    python_versions.append(Version("4.0"))


def format_version_specifier_poetry(requirement: Requirement) -> str:
    if not requirement.version_or_url:
        return "*"
    assert isinstance(
        requirement.version_or_url, list
    ), f"Only version specifiers are supported: {requirement.version_or_url}"
    specifiers = sorted(
        requirement.version_or_url, key=lambda specifier: specifier.version
    )
    # Easier to replace the whitespace than custom star formatting rules here
    poetry_specifiers = []
    for specifier in specifiers:
        poetry_specifier = str(specifier).replace(".dev", "dev")
        if poetry_specifier.endswith("dev0"):
            poetry_specifier = poetry_specifier.replace("dev0", "dev")
        poetry_specifiers.append(poetry_specifier)
    return ",".join(poetry_specifiers)


packages_toml = []
for (name, version), package_data in sorted(resolution.package_data.items()):
    requirements = sorted(
        package_data.requirements, key=lambda requirement: normalize(requirement.name)
    )
    requirements_reachable = list(
        filter(
            lambda requirement: requirement.evaluate_extras_and_python_version(
                package_data.extras, python_versions
            ),
            requirements,
        )
    )
    files = [
        {"file": file.filename, "hash": f"sha256:{file.hashes.sha256}"}
        for file in package_data.files
    ]
    extra_to_packages = defaultdict(list)
    optional_deps = set()
    for requirement in requirements:
        extras = pypi_types.collect_extras(requirement)
        if extras:
            optional_deps.add(normalize(requirement.name))
        for extra in extras:
            # I'm not sure what poetry is actually saving here
            # poetry uses a different PEP 508 normalization than we do, so patch this up
            if requirement.extras:
                extras_str = "[" + ",".join(requirement.extras) + "]"
            else:
                extras_str = ""
            if requirement.version_or_url:
                extra_str = (
                    f"{requirement.name}{extras_str} "
                    f"({format_version_specifier_poetry(requirement)})"
                )
            else:
                extra_str = f"{requirement.name}{extras_str}"

            extra_to_packages[normalize(extra)].append(extra_str)
    extra_to_packages = dict(sorted(extra_to_packages.items()))

    dependencies = {}
    for requirement in requirements_reachable:
        if ";" in str(requirement):
            # TODO(konstin): expose marker from python and implement poetry
            #  normalization
            markers = str(requirement).split(";")[1].strip().replace("'", r'"')
            expanded_deb = {"version": format_version_specifier_poetry(requirement)}
            if requirement.extras:
                # TODO(konstin): normalize
                expanded_deb["extras"] = requirement.extras
            if normalize(requirement.name) in optional_deps:
                expanded_deb["optional"] = True
            expanded_deb["markers"] = markers
            dependencies[requirement.name] = expanded_deb

        else:
            dependencies[requirement.name] = format_version_specifier_poetry(
                requirement
            )

    # TODO: Actually store some metadata
    python_version = "*"  # str(package_data.metadata.requires_python or "*")
    data = {
        "name": name,
        "version": str(version),
        "description": "",  # TODO: Keep summary around
        "category": "main",
        "optional": False,
        # poetry version specifier normalization
        "python-versions": python_version,
        "files": files,
    }
    if dependencies:
        data["dependencies"] = dependencies
    if extra_to_packages:
        data["extras"] = {
            extra: sorted(packages) for extra, packages in extra_to_packages.items()
        }

    packages_toml.append(data)
lock_data = {
    "package": packages_toml,
    "metadata": {
        "lock-version": "2.0",
        "python-versions": str(requires_python),
        "content-hash": "",
    },
}
resolutions_ours.joinpath(f"{root_requirement}.lock.toml").write_text(
    tomli_w.dumps(lock_data)
)
# Roundtrip to get the same formatting
existing = tomli.loads(
    resolutions_poetry.joinpath(f"{root_requirement}")
    .joinpath("poetry.lock")
    .read_text()
)
resolutions_poetry.joinpath(f"{root_requirement}").joinpath(
    "poetry.lock.toml"
).write_text(tomli_w.dumps(existing))
# print(tomli_w.dumps(lock_data))

if __name__ == "__main__":
    pass
