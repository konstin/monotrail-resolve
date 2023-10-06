import os
from concurrent.futures import Executor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import httpx
import orjson
import pytest
import requests
import respx
from httpx import Response, AsyncClient
from respx import MockRouter
from zstandard import decompress, compress

from pypi_types import pypi_releases, filename_to_version, core_metadata
from pypi_types.pep440_rs import VersionSpecifiers
from pypi_types.pep508_rs import Requirement
from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.resolve import resolve_requirement, Resolution
from resolve_prototype.metadata import parse_requirement_fixup

update_snapshots = os.environ.get("UPDATE_SNAPSHOTS")
assert_all_mocked = not update_snapshots
assert_all_called = not update_snapshots


class TrimmedMetadataCache(Cache):
    """Only save METADATA files (the range requests are hard to mock)"""

    def set(self, bucket: str, name: str, content: str):
        if bucket == "wheel_metadata":
            # crudely remove the Description which is a lot of data we don't need in
            # the repo
            content = content.split("\n\n")[0]
            return super().set(bucket, name, content)


class DummyExecutor(Executor):
    """https://stackoverflow.com/a/60109361/3549270"""

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        pass

    # noinspection PyArgumentList
    def submit(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def shutdown(self, wait=True, **kwargs):
        pass


def test_handle_filename():
    assert filename_to_version("jedi", "jedi-0.8.0-final0.tar.gz") == "0.8.0-final0"
    assert filename_to_version("typed-ast", "typed-ast-0.5.1.tar.gz") == "0.5.1"
    assert filename_to_version("typed-ast", "typed_ast-0.5.1.tar.gz") == "0.5.1"


def test_parse_requirement_fixup(caplog):
    correct = parse_requirement_fixup(
        "elasticsearch-dsl (>=7.2.0,<8.0.0)", "django-elasticsearch-dsl 7.2.2"
    )
    assert caplog.messages == []
    wrong = parse_requirement_fixup(
        "elasticsearch-dsl (>=7.2.0<8.0.0)", "django-elasticsearch-dsl 7.2.2"
    )
    assert caplog.messages == [
        "Requirement `elasticsearch-dsl (>=7.2.0<8.0.0)` for django-elasticsearch-dsl"
        " 7.2.2 is invalid (missing comma)"
    ]
    assert wrong.version_or_url == correct.version_or_url


def httpx_mock_impl(path: Path, request: httpx.Request) -> httpx.Response:
    if update_snapshots and not path.is_file():
        # Passthrough case
        response = requests.get(request.url, headers=request.headers)
        response.raise_for_status()
        data = response.json()
        # Remove a larger chunk of the data
        del data["info"]["description"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".json.zst").write_bytes(
            compress(orjson.dumps(data), level=10)
        )

    if path:
        # A json roundtrip here is measurably slower
        uncompressed = decompress(path.with_suffix(".json.zst").read_bytes())
        return Response(200, content=uncompressed)
    else:
        raise FileNotFoundError(f"No mock at {path}")


def httpx_mock_cache_impl(
    data_dir: Path,
    request: httpx.Request,
    name: str,
    cache: dict[str, Any],
    file_stem: str,
) -> httpx.Response:
    path = data_dir.joinpath(file_stem).with_suffix(".json.zst")

    if not path.is_file() and update_snapshots:
        path.write_bytes(compress(orjson.dumps({}), level=10))

    if not cache:
        cache.update(orjson.loads(decompress(path.read_bytes())))

    if saved := cache.get(name):
        return Response(200, json=saved)
    elif update_snapshots:
        # Passthrough case
        response = requests.get(request.url, headers=request.headers)
        response.raise_for_status()
        saved = response.json()
        cache[name] = saved
        path.write_bytes(compress(orjson.dumps(cache), level=10))
        return Response(200, json=saved)
    else:
        raise FileNotFoundError(f"Missing mock for {name} at {path}")


class HttpMock:
    cache_simple: dict[str, Any]
    cache_json_metadata: dict[str, Any]
    data_dir: Path

    def __init__(self, rootpath: Path, test_name: str):
        self.cache_simple = {}
        self.cache_json_metadata = {}

        self.data_dir = rootpath.joinpath("test-data").joinpath(test_name)
        self.data_dir.mkdir(exist_ok=True)

    def httpx_mock_simple(self, request: httpx.Request, name: str) -> httpx.Response:
        # This should probably be a fixture or class instead
        return httpx_mock_cache_impl(
            self.data_dir, request, name, self.cache_simple, "simple"
        )

    def httpx_mock_json_metadata(
        self, request: httpx.Request, name: str, version: str
    ) -> httpx.Response:
        # This should probably be a fixture or class instead
        return httpx_mock_cache_impl(
            self.data_dir,
            request,
            f"{name} {version}",
            self.cache_json_metadata,
            "json_metadata",
        )

    def add_mocks(self, respx_mock: MockRouter):
        route = respx_mock.route(
            url__regex=r"https://pypi.org/simple/(?P<name>[\w\d_-]+)/\?format=application/vnd.pypi.simple.v1\+json"
        )
        route.side_effect = self.httpx_mock_simple
        route = respx_mock.route(
            url__regex=r"https://pypi.org/pypi/(?P<name>[\w\d_-]+)/(?P<version>[\w\d_.-]+)/json"
        )
        if update_snapshots:
            respx_mock.route(host="files.pythonhosted.org").pass_through()
        route.side_effect = self.httpx_mock_json_metadata


class SdistMetadataMock:
    rootpath: Path
    test_name: str
    data: dict[str, str]
    datafile: Path

    def __init__(self, test_name: str, rootpath: Path) -> None:
        self.test_name = test_name
        self.rootpath = rootpath
        self.datafile = (
            rootpath.joinpath("test-data")
            .joinpath(test_name)
            .joinpath("sdist_metadata")
            .with_suffix(".json.zst")
        )
        if update_snapshots and not self.datafile.is_file():
            self.data = {}
        else:
            self.data = orjson.loads(decompress(self.datafile.read_bytes()))

    async def mock_build_sdist(
        self, client: AsyncClient, file: pypi_releases.File, _cache: Cache
    ) -> core_metadata.Metadata21:
        if update_snapshots:
            with TemporaryDirectory() as tempdir:
                from resolve_prototype.sdist import build_sdist_impl

                metadata_path = await build_sdist_impl(client, file, tempdir)
                metadata = core_metadata.Metadata21.read(
                    str(metadata_path), file.filename
                )
                self.data[file.filename] = metadata_path.read_text()
            self.datafile.write_bytes(compress(orjson.dumps(self.data), level=10))
            return metadata

        if capture := self.data.get(file.filename):
            return core_metadata.Metadata21.from_bytes(capture.encode())
        else:
            raise RuntimeError(f"Missing sdist metadata snapshot for {file.filename}")


def assert_resolution(resolution: Resolution, rootpath: Path, name: str):
    frozen = "\n".join(
        sorted(f"{name}=={version}" for name, version in resolution.package_data)
    )
    requirements_txt = (
        rootpath.joinpath("test-data").joinpath(name).joinpath("requirements.txt")
    )
    if update_snapshots:
        requirements_txt.write_text(frozen)
    assert frozen == requirements_txt.read_text()


@pytest.mark.asyncio()
@respx.mock(assert_all_mocked=assert_all_mocked, assert_all_called=assert_all_called)
async def test_pandas(respx_mock: MockRouter, pytestconfig: pytest.Config):
    """Simplest case, doesn't use any sdists"""
    http_mock = HttpMock(pytestconfig.rootpath, "pandas")
    http_mock.add_mocks(respx_mock)

    requires_python = VersionSpecifiers(">= 3.8")
    resolution = await resolve_requirement(
        Requirement("pandas"),
        requires_python,
        TrimmedMetadataCache(default_cache_dir, read=False, write=False),
        download_wheels=False,
    )
    assert_resolution(resolution, pytestconfig.rootpath, "pandas")


@pytest.mark.asyncio()
@respx.mock(assert_all_mocked=assert_all_mocked, assert_all_called=assert_all_called)
async def test_meine_stadt_transparent(
    respx_mock: MockRouter, pytestconfig: pytest.Config
):
    http_mock = HttpMock(pytestconfig.rootpath, "meine_stadt_transparent")
    http_mock.add_mocks(respx_mock)
    sdist_metadata_mock = SdistMetadataMock(
        "meine_stadt_transparent", pytestconfig.rootpath
    )
    with patch(
        "resolve_prototype.sdist.build_sdist", sdist_metadata_mock.mock_build_sdist
    ):
        requires_python = VersionSpecifiers(">= 3.8")
        resolution = await resolve_requirement(
            Requirement("meine_stadt_transparent"),
            requires_python,
            TrimmedMetadataCache(default_cache_dir, read=False, write=False),
            download_wheels=False,
        )
    assert_resolution(resolution, pytestconfig.rootpath, "meine_stadt_transparent")


@pytest.mark.asyncio()
@respx.mock(assert_all_mocked=assert_all_mocked, assert_all_called=assert_all_called)
async def test_matplotlib(respx_mock: MockRouter, pytestconfig: pytest.Config):
    """Test wheel metadata downloading"""

    http_mock = HttpMock(pytestconfig.rootpath, "matplotlib")
    http_mock.add_mocks(respx_mock)
    cache_dir = pytestconfig.rootpath.joinpath("test-data").joinpath("fake_cache")
    requires_python = VersionSpecifiers(">= 3.8")
    resolution = await resolve_requirement(
        Requirement("matplotlib"),
        requires_python,
        TrimmedMetadataCache(cache_dir, read=True, write=True),
        download_wheels=True,
        executor=DummyExecutor,
    )
    assert_resolution(resolution, pytestconfig.rootpath, "matplotlib")
