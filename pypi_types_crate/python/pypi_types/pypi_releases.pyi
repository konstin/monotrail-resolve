class PypiReleases:
    files: list[File]
    meta: Meta
    name: str
    versions: list[str] | None

class File:
    filename: str
    hashes: Hashes
    requires_python: str | None
    size: int | None
    upload_time: str | None
    url: str
    yanked: bool | str

class Hashes:
    sha256: str

class Meta:
    last_serial: int | None
    api_version: str

def parse(text: str) -> PypiReleases: ...
