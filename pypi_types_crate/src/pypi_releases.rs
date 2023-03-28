use pyo3::types::PyModule;
use pyo3::{pyclass, pyfunction, pymodule, wrap_pyfunction, IntoPy, PyObject, PyResult, Python};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct PypiReleases {
    pub files: Vec<File>,
    pub meta: Meta,
    pub name: String,
    pub versions: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
#[serde(rename_all = "kebab-case")]
pub struct File {
    pub filename: String,
    pub hashes: Hashes,
    pub requires_python: Option<String>,
    pub size: Option<i64>,
    pub upload_time: Option<String>,
    pub url: String,
    // TODO: This either a bool (false) or a string with the reason
    pub yanked: Yanked,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[serde(untagged)]
pub enum Yanked {
    Bool(bool),
    Reason(String),
}

impl IntoPy<PyObject> for Yanked {
    fn into_py(self, py: Python<'_>) -> PyObject {
        match self {
            Yanked::Bool(bool) => bool.into_py(py),
            Yanked::Reason(reason) => reason.into_py(py),
        }
    }
}

impl Yanked {
    pub fn is_yanked(&self) -> bool {
        match self {
            Yanked::Bool(bool) => *bool,
            Yanked::Reason(_) => true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
pub struct Hashes {
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[pyclass(dict, get_all)]
#[serde(rename_all = "kebab-case")]
pub struct Meta {
    pub last_serial: Option<i64>,
    pub api_version: String,
}

#[pyfunction]
pub fn parse(text: &str) -> anyhow::Result<PypiReleases> {
    Ok(serde_json::from_str(text)?)
}

#[pymodule]
pub fn pypi_releases(_py: Python, module: &PyModule) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse, module)?)?;
    module.add_class::<PypiReleases>()?;
    module.add_class::<File>()?;
    module.add_class::<Hashes>()?;
    module.add_class::<Meta>()?;
    Ok(())
}
