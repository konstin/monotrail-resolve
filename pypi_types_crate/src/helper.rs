use crate::pypi_releases;
use once_cell::sync::Lazy;
use pep440_rs::Version;
use pyo3::exceptions::{PyFileNotFoundError, PyRuntimeError, PyValueError};
use pyo3::pyfunction;
use pyo3::PyResult;
use regex::Regex;
use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;
use std::path::PathBuf;
use std::str::FromStr;
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

/// Returns the releases (version -> filenames), the ignored filenames and the invalid versions
#[pyfunction]
#[allow(clippy::type_complexity)] // Newtype would be worse for pyo3
pub fn parse_releases_data(
    project: &str,
    filename: PathBuf,
) -> PyResult<(HashMap<Version, Vec<String>>, Vec<String>, Vec<String>)> {
    let mut releases: HashMap<Version, Vec<String>> = HashMap::new();
    let mut ignored_filenames: Vec<String> = Vec::new();
    let mut invalid_versions: Vec<String> = Vec::new();

    let reader = BufReader::new(
        File::open(filename).map_err(|err| PyFileNotFoundError::new_err(err.to_string()))?,
    );
    let data: pypi_releases::PypiReleases =
        serde_json::from_reader(reader).map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
    if !["1.0", "1.1"].contains(&data.meta.api_version.as_str()) {
        return Err(PyRuntimeError::new_err(
            "Unsupported api version {data.meta.api_version}",
        ));
    }

    for file in data.files {
        if file.yanked.is_yanked() {
            continue;
        }

        if let Some(version) = filename_to_version(project, &file.filename)? {
            match Version::from_str(&version) {
                Ok(version) => releases
                    .entry(version.clone())
                    .or_default()
                    .push(file.filename.clone()),
                Err(_) => invalid_versions.push(version),
            }
        } else {
            ignored_filenames.push(file.filename.clone());
        }
    }

    Ok((releases, ignored_filenames, invalid_versions))
}
