import json
import sys
import time
from pathlib import Path
from subprocess import check_call, check_output
from typing import Dict

from pypi_types import pep508_rs

from resolve_prototype.compare.common import resolutions_pip


def pip_venv_dir(root_requirement: pep508_rs.Requirement) -> Path:
    return resolutions_pip.joinpath(str(root_requirement) + ".venv")


def read_pip_report(root_requirement: pep508_rs.Requirement) -> Dict[str, str]:
    report_file = resolutions_pip.joinpath(f"{root_requirement}.json")
    report = json.loads(report_file.read_text())
    return {
        dep["metadata"]["name"]: dep["metadata"]["version"] for dep in report["install"]
    }


def pip_resolve(root_requirement: pep508_rs.Requirement) -> Dict[str, str]:
    assert "/" not in str(root_requirement)
    resolutions_pip.mkdir(exist_ok=True)
    venv_dir = pip_venv_dir(root_requirement)
    check_call(["virtualenv", "--clear", venv_dir])
    start = time.time()
    pip_bin = venv_dir.joinpath("bin").joinpath("pip")
    report_file = resolutions_pip.joinpath(f"{root_requirement}.json")
    pip_output = check_output(
        [
            pip_bin,
            "install",
            "--report",
            report_file,
            "--dry-run",
            str(root_requirement),
        ],
        text=True,
    )
    end = time.time()
    print(f"resolution pip took {end - start:.3f}s")
    resolutions_pip.joinpath(f"{root_requirement}.output.txt").write_text(pip_output)
    return read_pip_report(root_requirement)


def main():
    root_requirement = pep508_rs.Requirement(sys.argv[1])
    resolution = pip_resolve(root_requirement)
    frozen = "".join([f"{name}=={version}\n" for name, version in resolution])
    resolutions_pip.joinpath(root_requirement.name).with_suffix(".txt").write_text(
        frozen
    )


if __name__ == "__main__":
    main()
