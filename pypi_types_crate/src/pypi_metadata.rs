use crate::Opaque;
use pyo3::basic::CompareOp;
use pyo3::exceptions::PyNotImplementedError;
use pyo3::types::PyModule;
use pyo3::{
    pyclass, pyfunction, pymethods, pymodule, wrap_pyfunction, IntoPy, PyObject, PyResult, Python,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Welcome {
    pub info: Metadata,
    pub last_serial: i64,
    pub urls: Vec<Url>,
    pub vulnerabilities: Vec<Vulnerability>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Metadata {
    pub author: Option<String>,
    pub author_email: Option<String>,
    pub bugtrack_url: Option<Opaque>,
    pub classifiers: Option<Vec<String>>,
    pub description: Option<String>,
    pub description_content_type: Option<String>,
    pub docs_url: Option<Opaque>,
    pub download_url: Option<String>,
    pub downloads: Option<Downloads>,
    pub home_page: Option<String>,
    pub keywords: Option<StringOrVec>,
    pub license: Option<String>,
    pub maintainer: Option<String>,
    pub maintainer_email: Option<String>,
    pub name: String,
    pub package_url: Option<String>,
    pub platform: Option<StringOrVec>,
    pub project_url: Option<StringOrVec>,
    pub project_urls: Option<ProjectUrls>,
    pub release_url: Option<String>,
    pub requires_dist: Option<Vec<String>>,
    pub requires_python: Option<String>,
    pub summary: Option<String>,
    pub version: String,
    pub yanked: Option<bool>,
    pub yanked_reason: Option<Opaque>,
}

/// No idea what exactly is going on here
#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[serde(untagged)]
pub enum StringOrVec {
    String(String),
    Vec(Vec<String>),
}

impl IntoPy<PyObject> for StringOrVec {
    fn into_py(self, py: Python<'_>) -> PyObject {
        match self {
            StringOrVec::String(string) => string.into_py(py),
            StringOrVec::Vec(vec) => vec.into_py(py),
        }
    }
}

#[pymethods]
impl Metadata {
    #[staticmethod]
    fn from_name_and_requires_dist(name: String, requires_dist: Option<Vec<String>>) -> Self {
        Self {
            author: None,
            author_email: None,
            bugtrack_url: None,
            classifiers: None,
            description: None,
            description_content_type: None,
            docs_url: None,
            download_url: None,
            downloads: None,
            home_page: None,
            keywords: None,
            license: None,
            maintainer: None,
            maintainer_email: None,
            name,
            package_url: None,
            platform: None,
            project_url: None,
            project_urls: None,
            release_url: None,
            requires_dist,
            requires_python: None,
            summary: None,
            version: "".to_string(),
            yanked: None,
            yanked_reason: None,
        }
    }

    fn __richcmp__(&self, other: &Self, op: CompareOp) -> PyResult<bool> {
        if matches!(op, CompareOp::Eq) {
            Ok(self == other)
        } else if matches!(op, CompareOp::Ne) {
            Ok(self != other)
        } else {
            Err(PyNotImplementedError::new_err(
                "Can only compare Metadata by equality",
            ))
        }
    }

    /// TODO(konstin): For some reason `__dict__` doesn't work
    pub fn to_json_str(&self) -> anyhow::Result<String> {
        Ok(serde_json::to_string(&self)?)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Downloads {
    pub last_day: i64,
    pub last_month: i64,
    pub last_week: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct ProjectUrls {
    pub documentation: Option<String>,
    pub funding: Option<String>,
    pub homepage: Option<String>,
    pub release_notes: Option<String>,
    pub source: Option<String>,
    pub tracker: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Url {
    pub comment_text: Option<String>,
    pub digests: Digests,
    pub downloads: i64,
    pub filename: String,
    pub has_sig: bool,
    pub md5_digest: String,
    pub packagetype: String,
    pub python_version: String,
    pub requires_python: Option<String>,
    pub size: i64,
    pub upload_time: String,
    pub upload_time_iso_8601: String,
    pub url: String,
    pub yanked: bool,
    pub yanked_reason: Option<Opaque>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Digests {
    pub blake2_b_256: Option<String>,
    pub md5: String,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Vulnerability {
    pub aliases: Vec<String>,
    pub details: String,
    pub fixed_in: Vec<String>,
    pub id: String,
    pub link: String,
    pub source: String,
    pub summary: Option<Opaque>,
    pub withdrawn: Option<Opaque>,
}

#[pyfunction]
pub fn parse(text: &str) -> anyhow::Result<Welcome> {
    Ok(serde_json::from_str(text)?)
}

#[pyfunction]
pub fn parse_metadata(text: &str) -> anyhow::Result<Metadata> {
    Ok(serde_json::from_str(text)?)
}

#[pymodule]
pub fn pypi_metadata(_py: Python, module: &PyModule) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse, module)?)?;
    module.add_function(wrap_pyfunction!(parse_metadata, module)?)?;
    module.add_class::<Welcome>()?;
    module.add_class::<Metadata>()?;
    module.add_class::<Downloads>()?;
    module.add_class::<ProjectUrls>()?;
    module.add_class::<Url>()?;
    module.add_class::<Digests>()?;
    module.add_class::<Vulnerability>()?;
    Ok(())
}
