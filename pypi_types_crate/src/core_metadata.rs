//! Parse the [core metadata](https://packaging.python.org/en/latest/specifications/core-metadata/)
//! from a METADATA file
//!
//! Based on maturin <https://github.com/PyO3/maturin/blob/3f032afe53a25d3a49bef0c5dcb4485ffc40dff5/src/metadata.rs>
//! and python-pkg-info-rs <https://github.com/PyO3/python-pkginfo-rs/blob/d719988323a0cfea86d4737116d7917f30e819e2/src/metadata.rs#LL78C2-L91C26>

use mailparse::{MailHeaderMap, MailParseError};
use once_cell::sync::Lazy;
use pep440_rs::{Pep440Error, Version, VersionSpecifiers};
use pep508_rs::{Pep508Error, Requirement};
use pyo3::types::PyModule;
use pyo3::{pyclass, pymethods, pymodule, PyResult, Python};
use regex::Regex;
use serde::{de, Deserialize, Deserializer, Serialize};
use std::collections::HashMap;
use std::str::FromStr;
use std::{fs, io};
use thiserror::Error;
use tracing::warn;

/// See [parse_requirement_with_fixup]
static REQUIREMENT_FIXUP_REGEX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d)([<>=~^!])").unwrap());

/// Fixes unfortunately popular missing comma errors such as `elasticsearch-dsl (>=7.2.0<8.0.0)` in
/// django-elasticsearch-dsl 7.2.2 with a regex heuristic
pub fn parse_requirement_with_fixup(
    requirement_str: &str,
    debug_str: Option<&str>,
) -> Result<Requirement, Pep508Error> {
    let result = Requirement::from_str(requirement_str);
    if let Ok(requirement) = result {
        Ok(requirement)
    } else {
        let patched = REQUIREMENT_FIXUP_REGEX.replace(requirement_str, r"$1,$2");
        if let Ok(requirement) = Requirement::from_str(&patched) {
            if let Some(debug_str) = debug_str {
                warn!(
                    "Requirement `{}` from {} is invalid (missing comma). Using workaround.",
                    debug_str, requirement
                );
            } else {
                warn!(
                    "Requirement `{}` is invalid (missing comma). Using workaround.",
                    requirement
                );
            }
            Ok(requirement)
        } else {
            // Didn't work with the fixup either? raise the error with the original string
            result
        }
    }
}

fn parse_requirement_with_fixup_serde<'de, D>(deserializer: D) -> Result<Requirement, D::Error>
where
    D: Deserializer<'de>,
{
    let requirement_str = String::deserialize(deserializer)?;
    parse_requirement_with_fixup(&requirement_str, None).map_err(de::Error::custom)
}

/// https://github.com/serde-rs/serde/issues/723#issuecomment-382501277
fn deserialize_requirements_with_fixup<'de, D>(
    deserializer: D,
) -> Result<Vec<Requirement>, D::Error>
where
    D: Deserializer<'de>,
{
    #[derive(Deserialize)]
    struct Wrapper(#[serde(deserialize_with = "parse_requirement_with_fixup_serde")] Requirement);

    let requirements = Vec::deserialize(deserializer)?;
    Ok(requirements.into_iter().map(|Wrapper(a)| a).collect())
}

/// Python Package Metadata 2.1 as specified in
/// <https://packaging.python.org/specifications/core-metadata/>
///
/// One addition is the requirements fixup which insert missing commas e.g. in
/// `elasticsearch-dsl (>=7.2.0<8.0.0)`
#[derive(Serialize, Deserialize, Debug, Clone, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
#[allow(missing_docs)]
#[pyclass(get_all)]
pub struct Metadata21 {
    // Mandatory fields
    pub metadata_version: String,
    pub name: String,
    pub version: Version,
    // Optional fields
    pub platforms: Vec<String>,
    pub supported_platforms: Vec<String>,
    pub summary: Option<String>,
    pub description: Option<String>,
    pub description_content_type: Option<String>,
    pub keywords: Option<String>,
    pub home_page: Option<String>,
    pub download_url: Option<String>,
    pub author: Option<String>,
    pub author_email: Option<String>,
    pub maintainer: Option<String>,
    pub maintainer_email: Option<String>,
    pub license: Option<String>,
    pub classifiers: Vec<String>,
    #[serde(deserialize_with = "deserialize_requirements_with_fixup")]
    pub requires_dist: Vec<Requirement>,
    pub provides_dist: Vec<String>,
    pub obsoletes_dist: Vec<String>,
    pub requires_python: Option<VersionSpecifiers>,
    pub requires_external: Vec<String>,
    pub project_urls: HashMap<String, String>,
    pub provides_extras: Vec<String>,
}

/// <https://github.com/PyO3/python-pkginfo-rs/blob/d719988323a0cfea86d4737116d7917f30e819e2/src/error.rs>
///
/// The error type
#[derive(Error, Debug)]
pub enum Error {
    /// I/O error
    #[error(transparent)]
    Io(#[from] io::Error),
    /// mail parse error
    #[error(transparent)]
    MailParse(#[from] MailParseError),
    /// Metadata field not found
    #[error("metadata field {0} not found")]
    FieldNotFound(&'static str),
    /// Unknown distribution type
    #[error("unknown distribution type")]
    UnknownDistributionType,
    /// Metadata file not found
    #[error("metadata file not found")]
    MetadataNotFound,
    /// Invalid project URL (no comma)
    #[error("Invalid Project-URL field (missing comma): '{0}'")]
    InvalidProjectUrl(String),
    /// Multiple metadata files found
    #[error("found multiple metadata files: {0:?}")]
    MultipleMetadataFiles(Vec<String>),
    /// Invalid Version
    #[error("invalid version: {0}")]
    Pep440VersionError(String),
    /// Invalid VersionSpecifier
    #[error(transparent)]
    Pep440Error(#[from] Pep440Error),
    /// Invalid Requirement
    #[error(transparent)]
    Pep508Error(#[from] Pep508Error),
}

#[pymethods]
impl Metadata21 {
    #[staticmethod]
    pub fn read(path: &str, debug_src: Option<String>) -> PyResult<Self> {
        let data = fs::read(path)?;
        // TODO(konstin): Forward the error properly
        Ok(Self::parse(&data, debug_src).map_err(anyhow::Error::from)?)
    }

    #[staticmethod]
    pub fn from_bytes(data: &[u8], debug_src: Option<String>) -> PyResult<Self> {
        // TODO(konstin): Forward the error properly
        Ok(Self::parse(data, debug_src).map_err(anyhow::Error::from)?)
    }
}

/// From <https://github.com/PyO3/python-pkginfo-rs/blob/d719988323a0cfea86d4737116d7917f30e819e2/src/metadata.rs#LL78C2-L91C26>
impl Metadata21 {
    /// Parse distribution metadata from metadata bytes
    pub fn parse(content: &[u8], debug_src: Option<String>) -> Result<Self, Error> {
        // HACK: trick mailparse to parse as UTF-8 instead of ASCII
        let mut mail = b"Content-Type: text/plain; charset=utf-8\n".to_vec();
        mail.extend_from_slice(content);

        let msg = mailparse::parse_mail(&mail)?;
        let headers = msg.get_headers();
        let get_first_value = |name| {
            headers.get_first_header(name).and_then(|header| {
                match rfc2047_decoder::decode(header.get_value_raw()) {
                    Ok(value) => {
                        if value == "UNKNOWN" {
                            None
                        } else {
                            Some(value)
                        }
                    }
                    Err(_) => None,
                }
            })
        };
        let get_all_values = |name| {
            let values: Vec<String> = headers
                .get_all_values(name)
                .into_iter()
                .filter(|value| value != "UNKNOWN")
                .collect();
            values
        };
        let metadata_version = headers
            .get_first_value("Metadata-Version")
            .ok_or(Error::FieldNotFound("Metadata-Version"))?;
        let name = headers
            .get_first_value("Name")
            .ok_or(Error::FieldNotFound("Name"))?;
        let version = Version::from_str(
            &headers
                .get_first_value("Version")
                .ok_or(Error::FieldNotFound("Version"))?,
        )
        .map_err(Error::Pep440VersionError)?;
        let platforms = get_all_values("Platform");
        let supported_platforms = get_all_values("Supported-Platform");
        let summary = get_first_value("Summary");
        let body = msg.get_body()?;
        let description = if !body.trim().is_empty() {
            Some(body)
        } else {
            get_first_value("Description")
        };
        let keywords = get_first_value("Keywords");
        let home_page = get_first_value("Home-Page");
        let download_url = get_first_value("Download-URL");
        let author = get_first_value("Author");
        let author_email = get_first_value("Author-email");
        let license = get_first_value("License");
        let classifiers = get_all_values("Classifier");
        let requires_dist = get_all_values("Requires-Dist")
            .iter()
            .map(|requires_dist| parse_requirement_with_fixup(requires_dist, debug_src.as_deref()))
            .collect::<Result<Vec<_>, _>>()?;
        let provides_dist = get_all_values("Provides-Dist");
        let obsoletes_dist = get_all_values("Obsoletes-Dist");
        let maintainer = get_first_value("Maintainer");
        let maintainer_email = get_first_value("Maintainer-email");
        let requires_python = get_first_value("Requires-Python")
            .map(|requires_python| VersionSpecifiers::from_str(&requires_python))
            .transpose()?;
        let requires_external = get_all_values("Requires-External");
        let project_urls = get_all_values("Project-URL")
            .iter()
            .map(|name_value| match name_value.split_once(',') {
                None => Err(Error::InvalidProjectUrl(name_value.clone())),
                Some((name, value)) => Ok((name.to_string(), value.trim().to_string())),
            })
            .collect::<Result<_, _>>()?;
        let provides_extras = get_all_values("Provides-Extra");
        let description_content_type = get_first_value("Description-Content-Type");
        Ok(Metadata21 {
            metadata_version,
            name,
            version,
            platforms,
            supported_platforms,
            summary,
            description,
            keywords,
            home_page,
            download_url,
            author,
            author_email,
            license,
            classifiers,
            requires_dist,
            provides_dist,
            obsoletes_dist,
            maintainer,
            maintainer_email,
            requires_python,
            requires_external,
            project_urls,
            provides_extras,
            description_content_type,
        })
    }
}

#[pymodule]
pub fn core_metadata(_py: Python, module: &PyModule) -> PyResult<()> {
    module.add_class::<Metadata21>()?;
    Ok(())
}
