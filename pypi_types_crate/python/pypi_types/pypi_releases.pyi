from typing import List, Optional, Union

class PypiReleases:
    files: List[File]
    meta: Meta
    name: str
    versions: Optional[List[str]]

class File:
    filename: str
    hashes: Hashes
    requires_python: Optional[str]
    size: Optional[int]
    upload_time: Optional[str]
    url: str
    yanked: Union[bool, str]

class Hashes:
    sha256: str

class Meta:
    last_serial: Optional[int]
    api_version: str

def parse(text: str) -> PypiReleases: ...
