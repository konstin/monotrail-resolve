use once_cell::sync::Lazy;
use pyo3::exceptions::PyValueError;
use pyo3::pyfunction;
use pyo3::PyResult;
use regex::Regex;
use tracing::warn;

/// Adopted from the grammar at <https://peps.python.org/pep-0508/#extras>
static NORMALIZER: Lazy<Regex> = Lazy::new(|| Regex::new(r"[-_.]+").unwrap());

/// Normalize to minus
fn normalize(name: &str) -> String {
    NORMALIZER.replace_all(name, "-").to_lowercase()
}

/// So distribution filenames are a mess: While wheels always have the same
/// structure (ends with `.whl`, five parts in the stem separated by four `-` of which
/// the second is the version), sdist have only been specified in 2020. Before that,
/// filenames may be kinda ambiguous in the sense that `tokenizer-rt-1.0-final1.tar.gz`
/// is valid as well as `tokenizer-1.0.tar.gz`. That's why we try to match the suffix
/// `.tar.gz` and the prefix by normalizing package name and the same length in the
/// filename by https://peps.python.org/pep-0503/#normalized-names and then parse the
/// version out of the middle.
#[pyfunction]
pub fn filename_to_version(package_name: &str, filename: &str) -> PyResult<Option<String>> {
    if filename.ends_with(".whl") {
        // https://peps.python.org/pep-0491/#file-format
        match filename.split('-').collect::<Vec<&str>>().as_slice() {
            [_name, version, _python_tag, _abi_tag, _platform_tag]
            | [_name, version, _, _python_tag, _abi_tag, _platform_tag] => {
                Ok(Some(version.to_string()))
            }
            _ => Err(PyValueError::new_err(format!(
                "Invalid wheel filename: {}",
                filename
            ))),
        }
    } else if let Some(basename) = [".tar.gz", ".zip", ".tar.bz2", ".tgz"]
        .iter()
        .filter_map(|suffix| filename.strip_suffix(suffix))
        .next()
    {
        let basename_normalized = normalize(basename);
        let file_prefix = normalize(package_name) + "-";
        if basename_normalized.starts_with(&file_prefix) {
            // We have to do this manually here because we don't know how long to normalize
            let version = &basename[file_prefix.len()..];
            Ok(Some(version.to_string()))
        } else {
            return Err(PyValueError::new_err(format!(
                "Name mismatch: Expected '{}' to start with '{}'",
                basename_normalized, file_prefix
            )));
        }
    } else if [".exe", ".msi", ".egg", ".rpm"]
        .iter()
        .any(|suffix| filename.ends_with(suffix))
    {
        Ok(None)
    } else {
        warn!("Filename with unexpected extension: {}", filename);
        Ok(None)
    }
}
