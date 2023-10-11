from pypi_types.pep440_rs import Version

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

    @staticmethod
    def vec_to_json(data: list[File]) -> str: ...
    @staticmethod
    def vec_from_json(data: bytes) -> list[File]: ...

class Hashes:
    sha256: str

class Meta:
    last_serial: int | None
    api_version: str

def parse(text: bytes) -> PypiReleases: ...
def versions_from_json(_data: bytes) -> list[Version]: ...
