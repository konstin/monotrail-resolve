import shutil
import sys
import time
from pathlib import Path
from subprocess import check_call
from typing import List, Tuple

import packaging.requirements
from pep508_rs import Requirement

from resolve_prototype.compare.common import resolutions_poetry


def poetry_dir(root_requirement: Requirement) -> Path:
    return resolutions_poetry.joinpath(str(root_requirement))


def read_poetry_requirements_current(
    root_requirement: Requirement,
) -> List[Tuple[str, str]]:
    """Reads the exported poetry requirements filtered down to the current
    environment"""
    lines = (
        resolutions_poetry.joinpath(str(root_requirement))
        .joinpath("requirements_current.txt")
        .read_text()
        .splitlines()
    )
    name_version = []
    for line in lines:
        name_version.append(line.split("=="))
    return name_version


def poetry_resolve(
    root_requirement: Requirement,
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Creates the whole thing anew each time"""
    assert "/" not in str(root_requirement)
    work_dir = poetry_dir(root_requirement)
    if work_dir.is_dir():
        shutil.rmtree(work_dir)
    work_dir.mkdir(exist_ok=True, parents=True)
    # A requirement as name crashes poetry
    check_call(
        ["poetry", "init", "--no-interaction", "--name", "resolutions_poetry"],
        cwd=work_dir,
    )
    start = time.time()
    extras_args = []
    for extra in root_requirement.extras:
        extras_args.extend(["-E", extra])
    check_call(
        ["poetry", "add", "--lock", root_requirement.name, "-vvv"] + extras_args, cwd=work_dir
    )
    end = time.time()
    print(f"resolution poetry took {end - start:.3f}s")
    check_call(
        [
            "poetry",
            "export",
            "-o",
            work_dir.joinpath("requirements.txt"),
            "--without-hashes",
        ],
        cwd=work_dir,
    )

    # We don't actually read to lockfile but the requirements.txt so we can parse it
    # with python packaging to catch bugs in our env/marker implementation
    requirements_txt = poetry_dir(root_requirement).joinpath(f"requirements.txt")
    # all markers
    name_version_all = []
    # only current environment
    name_version_current = []
    for line in requirements_txt.read_text().splitlines():
        requirement = packaging.requirements.Requirement(line)
        version = str(requirement.specifier).replace("==", "")
        name_version_all.append((requirement.name, version))
        if requirement.marker.evaluate():
            # packaging doesn't give us the version
            name_version_current.append((requirement.name, version))

    frozen = "".join([f"{name}=={version}\n" for name, version in name_version_current])
    resolutions_poetry.joinpath(str(root_requirement)).joinpath(
        "requirements_current.txt"
    ).write_text(frozen)

    return name_version_all, name_version_current


def main():
    root_requirement = Requirement(sys.argv[1])
    poetry_resolve(root_requirement)
    print(
        resolutions_poetry.joinpath(str(root_requirement))
        .joinpath("requirements_current.txt")
        .read_text()
    )


if __name__ == "__main__":
    main()
