import email.parser
import logging
import time
from collections import defaultdict
from typing import Optional, List, BinaryIO, Dict
from zipfile import ZipFile

import httpx
from httpx import AsyncClient
from pep508_rs import Version
from pypi_types import pypi_metadata, pypi_releases

from resolve_prototype.common import user_agent, normalize, handle_filename, Cache

logger = logging.getLogger(__name__)


# noinspection PyAbstractClass
class RemoteZipFile(BinaryIO):
    """Pretend local zip file that is actually querying the pypi for the exact ranges of
    the file. Requirement is that the server supports range requests
    (https://developer.mozilla.org/en-US/docs/Web/HTTP/Range_requests)

    Only implements the methods actually called by zipfile for what we do, we're lying
    about the type here
    """

    url: str
    pos: int
    len: int
    user_agent = user_agent

    def __init__(self, client: httpx.Client, url: str):
        self.url = url
        self.pos = 0
        self.client = client

        response = self.client.head(self.url, headers={"user-agent": self.user_agent})
        response.raise_for_status()
        accept_ranges = response.headers.get("accept-ranges")
        assert (
            accept_ranges == "bytes"
        ), f"The server needs to `accept-ranges: bytes`, but it says {accept_ranges}"
        self.len = int(response.headers["content-length"])

    def seekable(self):
        return True

    def seek(self, offset: int, whence: int = 0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.len + offset
        else:
            raise ValueError(f"whence must be 0, 1 or 2 but it's {whence}")
        return self.pos

    def tell(self):
        return self.pos

    def read(self, size: Optional[int] = None):
        # Here we could also use an end-open range, but we already have the information,
        # so let's keep track locally (which we when in doubt we can trust over the server)
        if size:
            read_len = size
        else:
            read_len = self.len - self.pos
        # HTTP Ranges are zero-indexed and inclusive
        # https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Range
        # https://developer.mozilla.org/en-US/docs/Web/HTTP/Range_requests
        headers = {
            "Range": f"bytes={self.pos}-{self.pos + read_len - 1}",
            "user-agent": self.user_agent,
        }
        response = self.client.get(self.url, headers=headers)
        data = response.read()
        self.pos += read_len
        return data


async def get_releases(
    client: AsyncClient, project: str, cache: Cache
) -> Dict[Version, List[pypi_releases.File]]:
    assert "/" not in normalize(project)
    url = (
        f"https://pypi.org/simple/{normalize(project)}/"
        + "?format=application/vnd.pypi.simple.v1+json"
    )

    cached = cache.get("pypi_simple_releases", normalize(project) + ".json")
    if cached:
        logger.debug(f"Using cached releases for {url}")
        return parse_releases_data(project, cached)

    logger.debug(f"Querying releases from {url}")
    response = await client.get(url, headers={"user-agent": user_agent})
    response.raise_for_status()
    data = response.text
    cache.set("pypi_simple_releases", normalize(project) + ".json", data)
    return parse_releases_data(project, data)


def parse_releases_data(
    project: str, data: str
) -> Dict[Version, List[pypi_releases.File]]:
    data = pypi_releases.parse(data)
    # data = PypiSimpleResponse.parse_raw(data.encode())
    assert data.meta.api_version in [
        "1.0",
        "1.1",
    ], f"Unsupported api version {data.meta.api_version}"
    releases: Dict[Version, List[pypi_releases.File]] = defaultdict(list)
    ignored = list()
    invalid_versions = []
    for file in data.files:
        # true or some string reason
        if file.yanked:
            continue

        if version := handle_filename(project, file.filename):
            try:
                version = Version(version)
            except ValueError:
                invalid_versions.append(version)
                continue
            releases[version].append(file)
        else:
            ignored.append(file.filename)
    if invalid_versions:
        logger.debug(f"{project} has invalid versions: {invalid_versions}")
    logger.debug(f"Ignoring files with unknown extensions: {ignored}")
    logger.info(
        f"Found {project} with {len(releases)} releases "
        # 10 most recent versions
        f"{', '.join([str(release) for release in releases.keys()][::-1][:10])}"
        ", ..."
    )
    return dict(releases)


async def get_metadata(
    client: AsyncClient, project: str, version: Version, cache
) -> pypi_metadata.Metadata:
    url = f"https://pypi.org/pypi/{normalize(project)}/{version}/json"

    cached = cache.get(
        "pypi_json_version_metadata", f"{normalize(project)}@{version}.json"
    )
    if cached:
        logger.debug(f"Using cached metadata for {url}")
        text = cached
    else:
        response = await client.get(url, headers={"user-agent": user_agent})
        logger.debug(f"Querying metadata from {url}")
        response.raise_for_status()
        text = response.text
        cache.set(
            "pypi_json_version_metadata", f"{normalize(project)}@{version}.json", text
        )
    try:
        return pypi_metadata.parse(text).info
    except Exception as err:
        raise RuntimeError(
            f"Failed to parse metadata for {project} {version}, "
            f"this is most likely a bug"
        ) from err
    # data = json.loads(text)
    # return ProjectVersionJsonResponse(**data).info


def get_metadata_from_wheel(
    name: str, version: Version, filename: str, url: str, cache: Cache
) -> pypi_metadata.Metadata:
    metadata_path = f"{name}-{version}.dist-info/METADATA"
    start = time.time()
    # By PEP 440 version must contain any slashes or other weird characters
    # TODO: check if there are any windows-unfriendly characters
    # TODO: Better cache tag
    metadata_str = cache.get("wheel_metadata", f"{name}@{version}.metadata")
    if not metadata_str:
        # Create a new client because we're running in a thread
        with httpx.Client() as client:
            zipfile = ZipFile(RemoteZipFile(client, url))
            try:
                metadata_str = zipfile.read(metadata_path).decode()
            except KeyError:
                metadata_str = None
                for filename in zipfile.namelist():
                    # TODO: Check that there's actually exactly one dist info directory and METADATA file
                    if filename.count("/") == 1 and filename.endswith(
                        ".dist-info/METADATA"
                    ):
                        metadata_str = zipfile.read(filename).decode()
                        break
                if not metadata_str:
                    logger.warning(f"Missing METADATA file for {name} {version} {url}")
                    # return ProjectMetadata(name=name, requires_dist=[])
                    return pypi_metadata.Metadata.from_name_and_requires_dist(name, [])
        cache.set("wheel_metadata", f"{name}@{version}.metadata", metadata_str)
    metadata = email.parser.HeaderParser().parsestr(metadata_str)
    end = time.time()
    logger.debug(f"Getting metadata took {end - start:.2f}s from {url}")
    # return ProjectMetadata(name=name, requires_dist=metadata.get_all("Requires-Dist"))
    return pypi_metadata.Metadata.from_name_and_requires_dist(
        name, metadata.get_all("Requires-Dist")
    )
