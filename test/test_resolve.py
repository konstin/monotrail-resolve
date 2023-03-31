import json
import os
from concurrent.futures import Executor
from pathlib import Path
from typing import Optional, Dict, Any
from unittest.mock import patch

import httpx
import orjson
import pytest
import requests
import respx
from httpx import Response, AsyncClient
from respx import MockRouter
from zstandard import decompress, compress

from pypi_types import pypi_metadata, pypi_releases, filename_to_version
from pypi_types.pep440_rs import VersionSpecifier
from pypi_types.pep508_rs import Requirement
from resolve_prototype.common import Cache, default_cache_dir
from resolve_prototype.resolve import parse_requirement_fixup, resolve

assert_all_mocked = not os.environ.get("UPDATE_SNAPSHOTS")
assert_all_called = not os.environ.get("UPDATE_SNAPSHOTS")


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
    if os.environ.get("UPDATE_SNAPSHOTS") and not path.is_file():
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
    cache: Dict[str, Any],
    file_stem: str,
) -> httpx.Response:
    path = data_dir.joinpath(file_stem).with_suffix(".json.zst")

    if not path.is_file() and os.environ.get("UPDATE_SNAPSHOTS"):
        path.write_bytes(compress(orjson.dumps({}), level=10))

    if not cache:
        cache.update(orjson.loads(decompress(path.read_bytes())))

    if saved := cache.get(name):
        return Response(200, json=saved)
    elif os.environ.get("UPDATE_SNAPSHOTS"):
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
    cache_simple: Dict[str, Any]
    cache_json_metadata: Dict[str, Any]
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
        if os.environ.get("UPDATE_SNAPSHOTS"):
            respx_mock.route(
                url__regex=r"https://files.pythonhosted.org/packages/.*/.*/.*/.*.tar.gz"
            ).pass_through()
        route.side_effect = self.httpx_mock_json_metadata


class SdistMetadataMock:
    rootpath: Path
    test_name: str
    data: Optional[Dict[str, dict]]
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
        if os.environ.get("UPDATE_SNAPSHOTS") and not self.datafile.is_file():
            self.data = {}
        else:
            self.data = orjson.loads(decompress(self.datafile.read_bytes()))

    async def mock_build_sdist(
        self, client: AsyncClient, file: pypi_releases.File, _cache: Cache
    ) -> pypi_metadata.Metadata:
        if os.environ.get("UPDATE_SNAPSHOTS"):
            from resolve_prototype.sdist import build_sdist

            metadata = await build_sdist(
                client, file, Cache(default_cache_dir, read=False, write=False)
            )
            self.data[file.filename] = orjson.loads(metadata.to_json_str())
            self.datafile.write_bytes(compress(orjson.dumps(self.data), level=10))

        if capture := self.data.get(file.filename):
            return pypi_metadata.parse_metadata(json.dumps(capture))
        else:
            raise RuntimeError(f"Missing sdist metadata snapshot for {file.filename}")


@pytest.mark.asyncio
@respx.mock(assert_all_mocked=assert_all_mocked, assert_all_called=assert_all_called)
async def test_pandas(respx_mock: MockRouter, pytestconfig: pytest.Config):
    """Simplest case, doesn't use any sdists"""
    http_mock = HttpMock(pytestconfig.rootpath, "pandas")
    http_mock.add_mocks(respx_mock)

    requires_python = VersionSpecifier(">= 3.8")
    resolution = await resolve(
        Requirement("pandas"),
        requires_python,
        Cache(default_cache_dir, read=False, write=False),
        download_wheels=False,
    )
    packages = sorted((name, str(version)) for name, version in resolution.package_data)
    assert packages == [
        ("numpy", "1.24.1"),
        ("pandas", "1.5.2"),
        ("python-dateutil", "2.8.2"),
        ("pytz", "2022.7"),
        ("six", "1.16.0"),
    ]


@pytest.mark.asyncio
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
        "resolve_prototype.resolve.build_sdist", sdist_metadata_mock.mock_build_sdist
    ):
        requires_python = VersionSpecifier(">= 3.8")
        resolution = await resolve(
            Requirement("meine_stadt_transparent"),
            requires_python,
            Cache(default_cache_dir, read=False, write=False),
            download_wheels=False,
        )
    packages = sorted((name, str(version)) for name, version in resolution.package_data)
    assert packages == [
        ("ansicon", "1.89.0"),
        ("anyascii", "0.3.1"),
        ("arrow", "1.2.3"),
        ("asgiref", "3.6.0"),
        ("backports-zoneinfo", "0.2.1"),
        ("beautifulsoup4", "4.9.3"),
        ("blessed", "1.19.1"),
        ("certifi", "2022.12.7"),
        ("cffi", "1.15.1"),
        ("charset-normalizer", "2.1.1"),
        ("click", "8.1.3"),
        ("colorama", "0.4.6"),
        ("cryptography", "39.0.2"),
        ("defusedxml", "0.7.1"),
        ("django", "4.0.8"),
        ("django-allauth", "0.51.0"),
        ("django-anymail", "8.6"),
        ("django-csp", "3.7"),
        ("django-decorator-include", "3.0"),
        ("django-elasticsearch-dsl", "7.2.2"),
        ("django-environ", "0.9.0"),
        ("django-filter", "21.1"),
        ("django-geojson", "3.2.1"),
        ("django-modelcluster", "6.0"),
        ("django-permissionedforms", "0.1"),
        ("django-picklefield", "3.1"),
        ("django-q", "1.3.9"),
        ("django-q-sentry", "0.1.6"),
        ("django-settings-export", "1.2.1"),
        ("django-simple-history", "3.2.0"),
        ("django-taggit", "2.1.0"),
        ("django-treebeard", "4.6.0"),
        ("django-webpack-loader", "1.6.0"),
        ("django-widget-tweaks", "1.4.12"),
        ("djangorestframework", "3.14.0"),
        ("draftjs-exporter", "2.1.7"),
        ("elasticsearch", "7.10.1"),
        ("elasticsearch-dsl", "7.4.0"),
        ("et-xmlfile", "1.1.0"),
        ("flask", "2.2.2"),
        ("geoextract", "0.3.1"),
        ("geographiclib", "2.0"),
        ("geopy", "2.3.0"),
        ("gunicorn", "20.1.0"),
        ("html2text", "2020.1.16"),
        ("html5lib", "1.1"),
        ("icalendar", "4.1.0"),
        ("idna", "3.4"),
        ("importlib-metadata", "6.0.0"),
        ("itsdangerous", "2.1.2"),
        ("jinja2", "3.1.2"),
        ("jinxed", "1.2.0"),
        ("joblib", "1.2.0"),
        ("jsonfield", "3.1.0"),
        ("l18n", "2021.3"),
        ("markupsafe", "2.1.1"),
        ("meine-stadt-transparent", "0.2.14"),
        ("minio", "7.1.12"),
        ("mysqlclient", "2.1.1"),
        ("nltk", "3.8.1"),
        ("numpy", "1.24.1"),
        ("oauthlib", "3.2.2"),
        ("openpyxl", "3.0.10"),
        ("osm2geojson", "0.2.3"),
        ("pillow", "9.4.0"),
        ("psycopg2", "2.9.5"),
        ("pyahocorasick", "1.4.4"),
        ("pycparser", "2.21"),
        ("pyjwt", "2.6.0"),
        ("pypdf2", "2.12.1"),
        ("python-dateutil", "2.8.2"),
        ("python-slugify", "6.1.2"),
        ("python3-openid", "3.2.0"),
        ("pytz", "2022.7"),
        ("redis", "3.5.3"),
        ("regex", "2022.10.31"),
        ("requests", "2.28.1"),
        ("requests-oauthlib", "1.3.1"),
        ("scipy", "1.10.0"),
        ("sentry-sdk", "1.12.1"),
        ("shapely", "2.0.1"),
        ("six", "1.16.0"),
        ("soupsieve", "2.3.2.post1"),
        ("splinter", "0.17.0"),
        ("sqlparse", "0.4.3"),
        ("tablib", "3.3.0"),
        ("telepath", "0.3"),
        ("text-unidecode", "1.3"),
        ("tqdm", "4.64.1"),
        ("typing-extensions", "4.4.0"),
        ("tzdata", "2023.2"),
        ("unidecode", "1.3.6"),
        ("urllib3", "1.26.13"),
        ("wagtail", "3.0.3"),
        ("wand", "0.6.10"),
        ("wcwidth", "0.2.5"),
        ("webencodings", "0.5.1"),
        ("werkzeug", "2.2.2"),
        ("willow", "1.4.1"),
        ("xlrd", "2.0.1"),
        ("xlsxwriter", "3.0.5"),
        ("xlwt", "1.3.0"),
        ("zipp", "3.11.0"),
    ]


@pytest.mark.asyncio
@respx.mock(assert_all_mocked=assert_all_mocked, assert_all_called=assert_all_called)
async def test_matplotlib(respx_mock: MockRouter, pytestconfig: pytest.Config):
    """Test wheel metadata downloading"""

    http_mock = HttpMock(pytestconfig.rootpath, "matplotlib")
    http_mock.add_mocks(respx_mock)
    cache_dir = pytestconfig.rootpath.joinpath("test-data").joinpath("fake_cache")
    requires_python = VersionSpecifier(">= 3.8")
    resolution = await resolve(
        Requirement("matplotlib"),
        requires_python,
        Cache(cache_dir, read=True, write=False),
        download_wheels=True,
        executor=DummyExecutor,
    )
    packages = sorted((name, str(version)) for name, version in resolution.package_data)
    assert packages == [
        ("contourpy", "1.0.6"),
        ("cycler", "0.11.0"),
        ("fonttools", "4.38.0"),
        ("kiwisolver", "1.4.4"),
        ("matplotlib", "3.6.2"),
        ("numpy", "1.24.1"),
        ("packaging", "23.0"),
        ("pillow", "9.4.0"),
        ("pyparsing", "3.0.9"),
        ("python-dateutil", "2.8.2"),
        ("six", "1.16.0"),
    ]
