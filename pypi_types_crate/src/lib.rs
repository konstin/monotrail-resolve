use pyo3::types::PyModule;
use pyo3::{pyclass, pymodule, wrap_pyfunction, PyResult, Python};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub mod core_metadata;
pub mod helper;
pub mod marker_intersection;
pub mod normalized_marker_expression;
pub mod pypi_metadata;
pub mod pypi_releases;
pub mod version_intersection;

/// Hide something from python
#[pyclass]
#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
#[serde(transparent)]
pub struct Opaque(Value);

#[pymodule]
pub fn pypi_types(py: Python, module: &PyModule) -> PyResult<()> {
    // If some other module already initialized the log, that's ok
    #[allow(unused_must_use)]
    {
        pyo3_log::try_init();
    }

    module.add_function(wrap_pyfunction!(helper::filename_to_version, py)?)?;
    module.add_function(wrap_pyfunction!(helper::parse_releases_data, py)?)?;
    module.add_function(wrap_pyfunction!(helper::collect_extras, py)?)?;
    module.add_function(wrap_pyfunction!(helper::write_parsed_release_data, py)?)?;
    module.add_function(wrap_pyfunction!(helper::read_parsed_release_data, py)?)?;

    let core_metadata_module = PyModule::new(py, "core_metadata")?;
    core_metadata::core_metadata(py, core_metadata_module)?;
    module.add_submodule(core_metadata_module)?;

    let pypi_metadata_module = PyModule::new(py, "pypi_metadata")?;
    pypi_metadata::pypi_metadata(py, pypi_metadata_module)?;
    module.add_submodule(pypi_metadata_module)?;

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
