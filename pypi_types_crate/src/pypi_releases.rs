//! For data from e.g. <https://pypi.org/simple/tqdm/?format=application/vnd.pypi.simple.v1+json>

use pep440_rs::Version;
use pyo3::basic::CompareOp;
use pyo3::exceptions::PyNotImplementedError;
use pyo3::types::PyModule;
use pyo3::{
    pyclass, pyfunction, pymethods, pymodule, wrap_pyfunction, IntoPy, PyObject, PyResult, Python,
};
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

#[pymethods]
impl File {
    fn __richcmp__(&self, other: &Self, op: CompareOp) -> PyResult<bool> {
        if matches!(op, CompareOp::Eq) {
            Ok(self == other)
        } else if matches!(op, CompareOp::Ne) {
            Ok(self != other)
        } else {
            Err(PyNotImplementedError::new_err(
                "Can only compare File by equality",
            ))
        }
    }

    fn __str__(&self) -> String {
        self.filename.clone()
    }

    fn __repr__(&self) -> String {
        self.filename.clone()
    }

    #[staticmethod]
    fn vec_to_json(data: Vec<File>) -> anyhow::Result<String> {
        Ok(serde_json::to_string(&data)?)
    }

    #[staticmethod]
    fn vec_from_json(data: &[u8]) -> anyhow::Result<Vec<File>> {
        Ok(serde_json::from_slice(data)?)
    }
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
pub fn parse(data: &[u8]) -> anyhow::Result<PypiReleases> {
    Ok(serde_json::from_slice(data)?)
}

#[pyfunction]
pub fn versions_from_json(data: &[u8]) -> anyhow::Result<Vec<Version>> {
    Ok(serde_json::from_slice(data)?)
}

#[pymodule]
pub fn pypi_releases(_py: Python, module: &PyModule) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse, module)?)?;
    module.add_function(wrap_pyfunction!(versions_from_json, module)?)?;
    module.add_class::<PypiReleases>()?;
    module.add_class::<File>()?;
    module.add_class::<Hashes>()?;
    module.add_class::<Meta>()?;
    Ok(())
}
