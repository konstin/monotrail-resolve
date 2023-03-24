import asyncio
import logging
import sys
from typing import Dict, Tuple

from pep508_rs import Requirement, MarkerEnvironment

from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.pip_freeze import pip_resolve, pip_venv_dir, read_pip_report
from resolve_prototype.resolve import resolve, Resolution

logger = logging.getLogger(__name__)


def compare_with_pip(
    root_requirement: Requirement,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    # noinspection PyArgumentList
    env = MarkerEnvironment.current()

    if not pip_venv_dir(root_requirement).is_dir():
        logger.info(f"Resolving {root_requirement} with pip")
        pip_resolution = pip_resolve(root_requirement)
    else:
        logger.info(
            f"Reusing pip resolution for {root_requirement} "
            f"from {pip_venv_dir(root_requirement)}"
        )
        pip_resolution = read_pip_report(root_requirement)
    logger.info(f"Resolving {root_requirement} with ours")
    ours_resolution: Resolution = asyncio.run(
        resolve(root_requirement, Cache(default_cache_dir))
    )
    ours_resolution_env: Resolution = ours_resolution.for_environment(env, [])
    pip_resolution = {
        name.lower().replace("-", "_").replace(".", "_"): version
        for name, version in pip_resolution.items()
    }
    ours_resolution_env: Dict[str, str] = {
        name.lower().replace("-", "_").replace(".", "_"): str(version)
        for name, version in ours_resolution_env.packages
    }
    return ours_resolution_env, pip_resolution


def main():
    logging.basicConfig(level=logging.INFO)
    root_requirement = Requirement(sys.argv[1])
    ours_resolution, pip_resolution = compare_with_pip(root_requirement)

    if ours_resolution == pip_resolution:
        print("Resolutions identical")

    for ours_only in ours_resolution.keys() - pip_resolution.keys():
        print(f"ours only: {ours_only} {ours_resolution[ours_only]}")

    for pip_only in pip_resolution.keys() - ours_resolution.keys():
        print(f"pip only: {pip_only} {pip_resolution[pip_only]}")

    for shared in pip_resolution.keys() & ours_resolution.keys():
        if pip_resolution[shared] != ours_resolution[shared]:
            print(
                f"version mismatch {shared}: "
                f"pip {pip_resolution[shared]} "
                f"ours {ours_resolution[shared]}"
            )


if __name__ == "__main__":
    main()
