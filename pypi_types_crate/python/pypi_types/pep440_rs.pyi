# stubgen -p pep440_rs

from typing import Any, ClassVar

class Version:
    dev: Any
    epoch: Any
    post: Any
    pre: Any
    release: Any

    @classmethod
    def __init__(cls, *args, **kwargs) -> None: ...
    def any_prerelease(self, *args, **kwargs) -> Any: ...
    def is_dev(self, *args, **kwargs) -> Any: ...
    def is_local(self, *args, **kwargs) -> Any: ...
    def is_post(self, *args, **kwargs) -> Any: ...
    def is_pre(self, *args, **kwargs) -> Any: ...
    def parse_star(self, *args, **kwargs) -> Any: ...
    def __eq__(self, other) -> Any: ...
    def __ge__(self, other) -> Any: ...
    def __gt__(self, other) -> Any: ...
    def __hash__(self) -> Any: ...
    def __le__(self, other) -> Any: ...
    def __lt__(self, other) -> Any: ...
    def __ne__(self, other) -> Any: ...

class VersionSpecifier:
    __hash__: ClassVar[None] = ...

    @classmethod
    def __init__(cls, *args, **kwargs) -> None: ...
    def contains(self, *args, **kwargs) -> Any: ...
    def __contains__(self, other) -> Any: ...
    def __eq__(self, other) -> Any: ...
    def __ge__(self, other) -> Any: ...
    def __gt__(self, other) -> Any: ...
    def __le__(self, other) -> Any: ...
    def __lt__(self, other) -> Any: ...
    def __ne__(self, other) -> Any: ...

class VersionSpecifiers:
    @classmethod
    def __init__(cls, version_specifiers: str) -> None: ...
    def __str__(self): ...
    def __repr__(self): ...
    def __iter__(self): ...
    def __getitem__(self, item): ...
    def __len__(self): ...
