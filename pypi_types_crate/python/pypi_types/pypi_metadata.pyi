from typing import List, Optional, Any, Union

class Welcome:
    info: Metadata
    last_serial: int
    urls: List[Url]
    vulnerabilities: List[Vulnerability]

class Metadata:
    author: Optional[str]
    author_email: Optional[str]
    bugtrack_url: Optional[Any]
    classifiers: Optional[List[str]]
    description: Optional[str]
    description_content_type: Optional[str]
    docs_url: Optional[Any]
    download_url: Optional[str]
    downloads: Optional[Downloads]
    home_page: Optional[str]
    keywords: Optional[Union[str, List[str]]]
    license: Optional[str]
    maintainer: Optional[str]
    maintainer_email: Optional[str]
    name: str
    package_url: Optional[str]
    platform: Optional[Union[str, List[str]]]
    project_url: Optional[Union[str, List[str]]]
    project_urls: Optional[ProjectUrls]
    release_url: Optional[str]
    requires_dist: Optional[List[str]]
    requires_python: Optional[str]
    summary: Optional[str]
    version: str
    yanked: Optional[bool]
    yanked_reason: Optional[Any]

    @staticmethod
    def from_name_and_requires_dist(
        name: str, requires_dist: Optional[List[str]]
    ) -> "Metadata": ...
    def __eq__(self, other) -> Any: ...
    def to_json_str(self) -> str: ...

class Downloads:
    last_day: int
    last_month: int
    last_week: int

class ProjectUrls:
    documentation: Optional[str]
    funding: Optional[str]
    homepage: Optional[str]
    release_notes: Optional[str]
    source: Optional[str]
    tracker: Optional[str]

class Url:
    comment_text: Optional[str]
    digests: Digests
    downloads: int
    filename: str
    has_sig: bool
    md5_digest: str
    packagetype: str
    python_version: str
    requires_python: Optional[str]
    size: int
    upload_time: str
    upload_time_iso_8601: str
    url: str
    yanked: bool
    yanked_reason: Optional[Any]

class Digests:
    blake2_b_256: Optional[str]
    md5: str
    sha256: str

class Vulnerability:
    aliases: List[str]
    details: str
    fixed_in: List[str]
    id: str
    link: str
    source: str
    summary: Optional[Any]
    withdrawn: Optional[Any]

def parse(text: str) -> Welcome: ...
def parse_metadata(text: str) -> Metadata: ...
