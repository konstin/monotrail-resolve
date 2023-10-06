import asyncio
import json
import sys
import time
from pathlib import Path
from subprocess import check_output

from build import BuildBackendException
from tqdm import tqdm

from pypi_types.pep440_rs import VersionSpecifiers
from pypi_types.pep508_rs import Requirement
from resolve_prototype import resolve, Cache, default_cache_dir

root = Path(check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())


def main():
    pypi_top_json = root.joinpath("download").joinpath(
        "top-pypi-packages-30-days.min.json"
    )
    top_packages = [i["project"] for i in json.loads(pypi_top_json.read_text())["rows"]]
    failures = []
    for idx, project in enumerate(tqdm(top_packages[:2000], file=sys.stdout)):
        # deprecated sklearn package, use scikit-learn instead
        if idx > 50000:
            continue
        # print(f"Resolving {idx + 1}/{len(top_packages)} {project}")
        # TODO: Force latest version
        root_requirement = Requirement(project)
        requires_python = VersionSpecifiers(">=3.8,<3.12")
        time.time()
        try:
            asyncio.run(
                resolve(
                    root_requirement,
                    requires_python,
                    Cache(default_cache_dir, refresh_versions=False),
                )
            )
        except (RuntimeError, BuildBackendException) as e:
            failures.append(project)
            print(e)
            # print("Retrying with refresh")
            # resolution: Resolution = asyncio.run(
            #    resolve(
            #        root_requirement,
            #        requires_python,
            #        Cache(default_cache_dir, refresh_versions=True),
            #    )
            # )
            continue
        # print(freeze(resolution, root_requirement))
        time.time()
        # print(f"{project} {end - start:.3f}s")
    print(f"{len(failures)}: {failures}")


if __name__ == "__main__":
    main()
