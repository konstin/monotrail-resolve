"""Compares the resolution for the packages in inputs.txt against pip's resolution"""

from pathlib import Path

from pypi_types.pep508_rs import Requirement
from resolve_prototype.compare.compare_with_pip import compare_with_pip


def main():
    requirements = []
    for line in Path(__file__).parent.joinpath("inputs.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            requirements.append(Requirement(line.strip()))

    results = []
    for requirement in requirements:
        ours_resolution, pip_resolution = compare_with_pip(requirement)
        for dont_lock in ["setuptools", "wheel"]:
            if dont_lock in ours_resolution:
                ours_resolution.pop(dont_lock)

        if ours_resolution == pip_resolution:
            print(f"{requirement} resolution identical")
        else:
            print(f"{requirement} mismatch")

        results.append((requirement, ours_resolution, pip_resolution))

    print("\nCompare with `pip install --dry-run` results:")
    for requirement, ours_resolution, pip_resolution in results:
        print(requirement, "GOOD" if ours_resolution == pip_resolution else "BAD")
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
