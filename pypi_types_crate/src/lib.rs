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

    module.add_function(wrap_pyfunction!(helper::filename_to_version, py)?)?;
    module.add_function(wrap_pyfunction!(helper::parse_releases_data, py)?)?;
    module.add_function(wrap_pyfunction!(helper::collect_extras, py)?)?;

    let pypi_version_module = PyModule::new(py, "pypi_metadata")?;
    pypi_metadata::pypi_metadata(py, pypi_version_module)?;
    module.add_submodule(pypi_version_module)?;

    let pypi_releases_module = PyModule::new(py, "pypi_releases")?;
    pypi_releases::pypi_releases(py, pypi_releases_module)?;
    module.add_submodule(pypi_releases_module)?;

    let pep508_rs_module = PyModule::new(py, "pep508_rs")?;
    pep508_rs::python_module(py, pep508_rs_module)?;
    module.add_submodule(pep508_rs_module)?;

    let pep440_rs_module = PyModule::new(py, "pep440_rs")?;
    pep440_rs::python_module(py, pep440_rs_module)?;
    module.add_submodule(pep440_rs_module)?;

    Ok(())
}
