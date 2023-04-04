from typing import List, Optional, Dict

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
    platforms: List[str]
    supported_platforms: List[str]
    summary: Optional[str]
    description: Optional[str]
    description_content_type: Optional[str]
    keywords: Optional[str]
    home_page: Optional[str]
    download_url: Optional[str]
    author: Optional[str]
    author_email: Optional[str]
    maintainer: Optional[str]
    maintainer_email: Optional[str]
    license: Optional[str]
    classifiers: List[str]
    requires_dist: List[Requirement]
    provides_dist: List[str]
    obsoletes_dist: List[str]
    requires_python: Optional[VersionSpecifiers]
    requires_external: List[str]
    project_urls: Dict[str, str]
    provides_extras: List[str]

    @staticmethod
    def read(path: str, debug_src: Optional[str] = None) -> "Metadata21": ...
    @staticmethod
    def from_bytes(data: bytes, debug_src: Optional[str] = None) -> "Metadata21": ...
