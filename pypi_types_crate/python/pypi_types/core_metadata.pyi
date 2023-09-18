from pypi_types.pep440_rs import VersionSpecifiers, Version
from pypi_types.pep508_rs import Requirement

class Metadata21:
    """Python Package Metadata 2.1 as specified in
    https://packaging.python.org/specifications/core-metadata/"""

    # Mandatory fields
    metadata_version: str
    name: str
    version: Version
    # Optional fields
    platforms: list[str]
    supported_platforms: list[str]
    summary: str | None
    description: str | None
    description_content_type: str | None
    keywords: str | None
    home_page: str | None
    download_url: str | None
    author: str | None
    author_email: str | None
    maintainer: str | None
    maintainer_email: str | None
    license: str | None
    classifiers: list[str]
    requires_dist: list[Requirement]
    provides_dist: list[str]
    obsoletes_dist: list[str]
    requires_python: VersionSpecifiers | None
    requires_external: list[str]
    project_urls: dict[str, str]
    provides_extras: list[str]

    @staticmethod
    def read(path: str, debug_src: str | None = None) -> Metadata21: ...

    @staticmethod
    def from_bytes(data: bytes, debug_src: str | None = None) -> Metadata21: ...
