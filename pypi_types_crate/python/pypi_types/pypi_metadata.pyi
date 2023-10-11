from typing import Any

class Welcome:
    info: Metadata
    last_serial: int
    urls: list[Url]
    vulnerabilities: list[Vulnerability]

class Metadata:
    author: str | None
    author_email: str | None
    bugtrack_url: Any | None
    classifiers: list[str] | None
    description: str | None
    description_content_type: str | None
    docs_url: Any | None
    download_url: str | None
    downloads: Downloads | None
    home_page: str | None
    keywords: str | list[str] | None
    license: str | None
    maintainer: str | None
    maintainer_email: str | None
    name: str
    package_url: str | None
    platform: str | list[str] | None
    project_url: str | list[str] | None
    project_urls: ProjectUrls | None
    release_url: str | None
    requires_dist: list[str] | None
    requires_python: str | None
    summary: str | None
    version: str
    yanked: bool | None
    yanked_reason: Any | None

    @staticmethod
    def from_name_and_requires_dist(
        name: str, requires_dist: list[str] | None
    ) -> Metadata: ...
    def __eq__(self, other) -> Any: ...
    def to_json_str(self) -> str: ...

class Downloads:
    last_day: int
    last_month: int
    last_week: int

class ProjectUrls:
    documentation: str | None
    funding: str | None
    homepage: str | None
    release_notes: str | None
    source: str | None
    tracker: str | None

class Url:
    comment_text: str | None
    digests: Digests
    downloads: int
    filename: str
    has_sig: bool
    md5_digest: str
    packagetype: str
    python_version: str
    requires_python: str | None
    size: int
    upload_time: str
    upload_time_iso_8601: str
    url: str
    yanked: bool
    yanked_reason: Any | None

class Digests:
    blake2_b_256: str | None
    md5: str
    sha256: str

class Vulnerability:
    aliases: list[str]
    details: str
    fixed_in: list[str]
    id: str
    link: str
    source: str
    summary: Any | None
    withdrawn: Any | None

def parse(text: bytes) -> Welcome: ...
def parse_metadata(text: str) -> Metadata: ...
