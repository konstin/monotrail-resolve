import json
import logging
import random
import string
import time
from collections import defaultdict
from pathlib import Path
from typing import BinaryIO
import typing
from zipfile import ZipFile

import httpx
from httpx import AsyncClient

from pypi_types import (
    pypi_metadata,
    pypi_releases,
    pep440_rs,
    filename_to_version,
    core_metadata,
    write_parsed_release_data,
)
from pypi_types.pep440_rs import Version
from resolve_prototype.common import user_agent, normalize, Cache, NormalizedName

if typing.TYPE_CHECKING:
    from resolve_prototype.resolve import State

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
        assert accept_ranges == "bytes", (
            "The server needs to `accept-ranges: bytes`, "
            f"but it says {accept_ranges} for {url}"
        )
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

    def read(self, size: int | None = None):
        # Here we could also use an end-open range, but we already have the information,
        # so let's keep track locally (which we when in doubt we can trust over the
        # server)
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
    state: "State",
    client: AsyncClient,
    project: str,
    cache: Cache,
    refresh: bool = False,
) -> list[pep440_rs.Version]:
    assert "/" not in normalize(project)
    url = (
        f"https://pypi.org/simple/{normalize(project)}/"
        + "?format=application/vnd.pypi.simple.v1+json"
    )

    # normalize removes all dots in the name
    cached = cache.get_path("pypi_simple_releases", normalize(project))
    versions_json = (
        Path(cache.root_cache_dir)
        .joinpath("pypi_simple_releases")
        .joinpath(normalize(project))
        .joinpath("versions.json")
    )
    if cached and cached.is_dir() and not refresh and not cache.refresh_versions:
        # return [Version(version) for version in json.loads(versions_json.read_text())]
        return pypi_releases.versions_from_json(versions_json.read_bytes())

    logger.debug(f"Querying releases from {url}")

    headers = {"user-agent": user_agent}
    if cached:
        etag = cached.joinpath("etag.txt")
        if etag.is_file():
            headers["If-None-Match"] = etag.read_text().strip()

    response = await client.get(url, headers=headers)
    if response.status_code == 200:
        logger.debug(f"New response for {url}")
        data = response.text
        parsed_data = parse_releases_data(project, data.encode())

        temp_dir = (
            Path(cache.root_cache_dir)
            .joinpath("pypi_simple_releases")
            .joinpath("".join(random.sample(string.hexdigits, 16)))
        )
        temp_dir.mkdir(parents=True)

        temp_dir.joinpath("versions.json").write_text(
            json.dumps([str(version) for version in parsed_data])
        )
        # Set the etag last to be interruption safe, etag expects cached for 304
        # responses
        if etag := response.headers.get("etag"):
            temp_dir.joinpath("etag.txt").write_text(etag)

        for version, files in parsed_data.items():
            state.files_cache.setdefault(project, {})[version] = files
            temp_dir.joinpath(str(version) + ".json").write_text(
                pypi_releases.File.vec_to_json(files)
            )

        cache.set(
            "pypi_simple_releases",
            normalize(project) + ".json",
            write_parsed_release_data(parsed_data),
        )
        if cache.write:
            try:
                temp_dir.rename(
                    Path(cache.root_cache_dir)
                    .joinpath("pypi_simple_releases")
                    .joinpath(normalize(project))
                )
            except OSError:
                # Race condition, the other thread was faster
                # TODO: Check it's actually the right error, or even use locks
                pass

        return list(parsed_data)
    elif response.status_code == 304:
        assert etag.is_file(), "Server returned 304 without etag"
        logger.debug(f"Not modified, using cached for {url}")
        return [Version(version) for version in json.loads(versions_json.read_text())]
    else:
        response.raise_for_status()
        raise RuntimeError(f"Unexpected status: {response.status_code}")


def get_files_for_version(
    cache: Cache, project: str, version: Version
) -> list[pypi_releases.File]:
    """At this point, we already got the list of versions, so we know we hit the
    cache."""
    return pypi_releases.File.vec_from_json(
        Path(cache.root_cache_dir)
        .joinpath("pypi_simple_releases")
        .joinpath(normalize(project))
        .joinpath(str(version) + ".json")
        .read_bytes()
    )


def parse_releases_data(
    project: str, data: bytes
) -> dict[pep440_rs.Version, list[pypi_releases.File]]:
    data: pypi_releases.PypiReleases = pypi_releases.parse(data)
    assert data.meta.api_version in [
        "1.0",
        "1.1",
    ], f"Unsupported api version {data.meta.api_version}"
    releases: dict[pep440_rs.Version, list[pypi_releases.File]] = defaultdict(list)
    ignored = list()
    invalid_versions = []
    for file in data.files:
        # true or some string reason
        if file.yanked:
            continue

        if version := filename_to_version(project, file.filename):
            try:
                version = pep440_rs.Version(version)
            except ValueError:
                invalid_versions.append(version)
                continue
            releases[version].append(file)
        else:
            ignored.append(file.filename)
    if invalid_versions:
        logger.debug(f"{project} has invalid versions: {invalid_versions}")
    logger.debug(f"Ignoring files with unknown extensions: {ignored}")
    # 10 most recent versions
    top10 = [str(release) for release in list(releases.keys())[::-1][:10]]
    logger.debug(
        f"Found {project} with {len(releases)} releases {', '.join(top10)}, ..."
    )
    return dict(releases)


async def get_metadata(
    client: AsyncClient, project: str, version: pep440_rs.Version, cache: Cache
) -> pypi_metadata.Metadata:
    url = f"https://pypi.org/pypi/{normalize(project)}/{version}/json"

    cached = cache.get_bytes(
        "pypi_json_version_metadata", f"{normalize(project)}@{version}.json"
    )
    if cached:
        logger.debug(f"Using cached metadata for {url}")
        data = cached
    else:
        response = await client.get(url, headers={"user-agent": user_agent})
        logger.debug(f"Querying metadata from {url}")
        response.raise_for_status()
        data = response.text
        cache.set(
            "pypi_json_version_metadata", f"{normalize(project)}@{version}.json", data
        )
        data = data.encode()
    try:
        return pypi_metadata.parse(data).info
    except Exception as err:
        raise RuntimeError(
            f"Failed to parse metadata for {project} {version}, "
            "this is most likely a bug"
        ) from err


def get_metadata_from_wheel(
    name: NormalizedName,
    version: pep440_rs.Version,
    filename: str,
    url: str,
    cache: Cache,
) -> core_metadata.Metadata21 | RuntimeError:
    metadata_path = f"{name}-{version}.dist-info/METADATA"
    start = time.time()
    # By PEP 440 version must contain any slashes or other weird characters
    # TODO: check if there are any windows-unfriendly characters
    # TODO: Better cache tag
    metadata_json = cache.get_bytes("wheel_metadata", f"{filename.split('/')[0]}.json")
    if metadata_json:
        try:
            return core_metadata.Metadata21.from_json(metadata_json)
        except RuntimeError as err:
            # Let the caller across the thread pool executor handle the call
            return err

    logger.debug(f"Querying {url}")
    # Create a new client because we're running in a thread
    with httpx.Client() as client:
        zipfile = ZipFile(RemoteZipFile(client, url))
        try:
            metadata_bytes = zipfile.read(metadata_path)
        except KeyError:
            metadata_bytes = None
            for zipped_file in zipfile.namelist():
                # TODO: Check that there's actually exactly one dist info directory
                #       and METADATA file
                if zipped_file.count("/") == 1 and zipped_file.endswith(
                    ".dist-info/METADATA"
                ):
                    metadata_bytes = zipfile.read(zipped_file)
                    break
            if not metadata_bytes:
                raise RuntimeError(
                    f"Missing METADATA file for {name} {version} {filename} {url}"
                ) from None

    metadata = core_metadata.Metadata21.from_bytes(metadata_bytes)
    cache.set("wheel_metadata", f"{filename.split('/')[0]}.json", metadata.to_json())
    end = time.time()
    logger.debug(f"Getting metadata took {end - start:.2f}s from {url}")
    try:
        return metadata
    except RuntimeError as err:
        # Let the caller across the thread pool executor handle the call
        return err
