import asyncio
import logging
import sys
from typing import Dict, Tuple

from pypi_types import pep508_rs
from pypi_types.pep440_rs import VersionSpecifier

from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.compare.poetry_lock import (
    poetry_dir,
    poetry_resolve,
    read_poetry_requirements_current,
)
from resolve_prototype.resolve import resolve, Resolution

logger = logging.getLogger(__name__)


def compare_with_poetry(
    root_requirement: pep508_rs.Requirement,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    # noinspection PyArgumentList
    env = pep508_rs.MarkerEnvironment.current()

    if not poetry_dir(root_requirement).is_dir():
        logger.info(f"Resolving {root_requirement} with poetry")
        _poetry_all, poetry_current = poetry_resolve(root_requirement)
    else:
        logger.info(
            f"Reusing poetry resolution for {root_requirement} "
            f"from {poetry_dir(root_requirement)}"
        )
        poetry_current = read_poetry_requirements_current(root_requirement)
    logger.info(f"Resolving {root_requirement} with ours")
    requires_python = VersionSpecifier(
        f"=={sys.version_info.major}.{sys.version_info.minor}"
    )
    ours_resolution: Resolution = asyncio.run(
        resolve(root_requirement, requires_python, Cache(default_cache_dir))
    )
    ours_resolution_env: Resolution = ours_resolution.for_environment(env, [])
    poetry_current = {
        name.lower().replace("-", "_").replace(".", "_"): version
        for name, version in poetry_current
    }
    ours_resolution_env: Dict[str, str] = {
        name.lower().replace("-", "_").replace(".", "_"): str(version)
        for name, version in ours_resolution_env.package_data
    }
    return ours_resolution_env, poetry_current


def main():
    logging.basicConfig(level=logging.INFO)
    root_requirement = pep508_rs.Requirement(sys.argv[1])
    ours_resolution, poetry_current = compare_with_poetry(root_requirement)

    if ours_resolution == poetry_current:
        print("Resolutions identical")

    for ours_only in ours_resolution.keys() - poetry_current.keys():
        print(f"ours only: {ours_only} {ours_resolution[ours_only]}")

    for poetry_only in poetry_current.keys() - ours_resolution.keys():
        print(f"poetry only: {poetry_only} {poetry_current[poetry_only]}")

    for shared in poetry_current.keys() & ours_resolution.keys():
        if poetry_current[shared] != ours_resolution[shared]:
            print(
                f"version mismatch {shared}: "
                f"poetry {poetry_current[shared]} "
                f"ours {ours_resolution[shared]}"
            )


if __name__ == "__main__":
    main()
