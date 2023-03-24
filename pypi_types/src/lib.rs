use helper::filename_to_version;
use pyo3::types::PyModule;
use pyo3::{pyclass, pymodule, wrap_pyfunction, PyResult, Python};
use serde::{Deserialize, Serialize};
use serde_json::Value;

mod helper;
mod pypi_metadata;
mod pypi_releases;

#[pyclass]
#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[serde(transparent)]
pub struct Opaque(Value);

#[pymodule]
pub fn pypi_types(py: Python, module: &PyModule) -> PyResult<()> {
    pyo3_log::init();

    let pypi_version_module = PyModule::new(py, "pypi_metadata")?;
    pypi_metadata::pypi_metadata(py, pypi_version_module)?;
    module.add_submodule(pypi_version_module)?;

    let pypi_releases_module = PyModule::new(py, "pypi_releases")?;
    pypi_releases::pypi_releases(py, pypi_releases_module)?;
    module.add_submodule(pypi_releases_module)?;

    module.add_function(wrap_pyfunction!(filename_to_version, py)?)?;

    Ok(())
}
