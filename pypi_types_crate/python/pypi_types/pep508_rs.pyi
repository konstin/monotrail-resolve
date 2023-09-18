# stubgen -p pep508_rs

from typing import Any

from pypi_types.pep440_rs import Version

class MarkerEnvironment:
    implementation_name: Any
    implementation_version: Any
    os_name: Any
    platform_machine: Any
    platform_python_implementation: Any
    platform_release: Any
    platform_system: Any
    platform_version: Any
    python_full_version: Any
    python_version: Any
    sys_platform: Any

    @classmethod
    def __init__(cls, *args, **kwargs) -> None: ...

    def current(self, *args, **kwargs) -> Any: ...

class Pep508Error(ValueError): ...

class Requirement:
    extras: list[str] | None
    name: str
    version_or_url: str | Version

    @classmethod
    def __init__(cls, *args, **kwargs) -> None: ...

    def evaluate_extras_and_python_version(self, *args, **kwargs) -> bool: ...

    def evaluate_markers(self, *args, **kwargs) -> bool: ...

    def evaluate_markers_and_report(self, *args, **kwargs) -> bool: ...

    def __eq__(self, other) -> Any: ...

    def __ge__(self, other) -> Any: ...

    def __gt__(self, other) -> Any: ...

    def __hash__(self) -> Any: ...

    def __le__(self, other) -> Any: ...

    def __lt__(self, other) -> Any: ...

    def __ne__(self, other) -> Any: ...
