use helper::filename_to_version;
use pyo3::types::PyModule;
use pyo3::{pymodule, wrap_pyfunction, PyResult, Python};

mod helper;

#[pymodule]
pub fn resolve_rs(py: Python, module: &PyModule) -> PyResult<()> {
    pyo3_log::init();

    module.add_function(wrap_pyfunction!(filename_to_version, py)?)?;

    Ok(())
}
