import asyncio
import logging
import sys

from pypi_types import pep508_rs
from pypi_types.pep440_rs import VersionSpecifiers
from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.compare.pip_freeze import (
    pip_resolve,
    pip_venv_dir,
    read_pip_report,
)
from resolve_prototype.resolve import resolve_requirement, Resolution

logger = logging.getLogger(__name__)


def compare_with_pip(
    root_requirement: pep508_rs.Requirement, python_version: tuple[int, int] = (3, 8)
) -> tuple[dict[str, str], dict[str, str]]:
    # noinspection PyArgumentList
    env = pep508_rs.MarkerEnvironment.current()
    # Don't look
    env = pep508_rs.MarkerEnvironment(
        implementation_name=env.implementation_name,
        implementation_version=env.implementation_version.string,
        os_name=env.os_name,
        platform_machine=env.platform_machine,
        platform_python_implementation=env.platform_python_implementation,
        platform_release=env.platform_release,
        platform_system=env.platform_system,
        platform_version=env.platform_version,
        python_full_version=env.python_full_version.string,
        python_version=f"{python_version[0]}.{python_version[1]}",
        sys_platform=env.sys_platform,
    )

    if not pip_venv_dir(root_requirement).is_dir():
        logger.info(f"Resolving {root_requirement} with pip")
        pip_resolution = pip_resolve(root_requirement, python_version)
    else:
        logger.info(
            f"Reusing pip resolution for {root_requirement} "
            f"from {pip_venv_dir(root_requirement)}"
        )
        pip_resolution = read_pip_report(root_requirement)
    logger.info(f"Resolving {root_requirement} with ours")
    requires_python = VersionSpecifiers(f"=={python_version[0]}.{python_version[1]}")
    ours_resolution: Resolution = asyncio.run(
        resolve_requirement(root_requirement, requires_python, Cache(default_cache_dir))
    )
    ours_resolution_env: Resolution = ours_resolution.for_environment(env, [])
    pip_resolution = {
        name.lower().replace("-", "_").replace(".", "_"): version
        for name, version in pip_resolution.items()
    }
    ours_resolution_env: dict[str, str] = {
        name.lower().replace("-", "_").replace(".", "_"): str(version)
        for name, version in ours_resolution_env.package_data
    }
    print(pip_resolution, ours_resolution_env)
    return ours_resolution_env, pip_resolution


def main():
    logging.basicConfig(level=logging.DEBUG)
    root_requirement = pep508_rs.Requirement(sys.argv[1])
    ours_resolution, pip_resolution = compare_with_pip(root_requirement)

    if ours_resolution == pip_resolution:
        print("Resolutions identical")

    for ours_only in sorted(ours_resolution.keys() - pip_resolution.keys()):
        print(f"ours only: {ours_only} {ours_resolution[ours_only]}")

    for pip_only in sorted(pip_resolution.keys() - ours_resolution.keys()):
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
