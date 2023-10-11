use once_cell::sync::Lazy;
use pep440_rs::Version;
use pep508_rs::{MarkerOperator, MarkerTree, MarkerValue, Requirement};
use pyo3::exceptions::{PyFileNotFoundError, PyRuntimeError, PyValueError};
use pyo3::pyfunction;
use pyo3::PyResult;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::BufReader;
use std::path::PathBuf;
use std::str::FromStr;
use tracing::warn;

use crate::pypi_releases;

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
/// filename by <https://peps.python.org/pep-0503/#normalized-names> and then parse the
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
            // Ignore invalid non-wheels such as kfp.tar.gz
            warn!(
                "Name mismatch: Expected '{}' to start with '{}', but it was '{}'",
                basename_normalized, file_prefix, filename
            );
            Ok(None)
        }
    } else if [".exe", ".msi", ".egg", ".rpm", ".dmg"]
        .iter()
        .any(|suffix| filename.ends_with(suffix))
    {
        Ok(None)
    } else {
        warn!("Filename with unexpected extension: {}", filename);
        Ok(None)
    }
}

#[pyfunction]
pub fn write_parsed_release_data(
    data: HashMap<Version, Vec<pypi_releases::File>>,
) -> anyhow::Result<String> {
    Ok(serde_json::to_string(&data)?)
}

/// For some reason, passing in a string is actually more performant than reading the file in rust
#[pyfunction]
pub fn read_parsed_release_data(
    data: &[u8],
) -> anyhow::Result<HashMap<Version, Vec<pypi_releases::File>>> {
    Ok(serde_json::from_slice(data)?)
}

/// Returns the releases (version -> filenames), the ignored filenames and the invalid versions
#[pyfunction]
#[allow(clippy::type_complexity)] // Newtype would be worse for pyo3
pub fn parse_releases_data(
    project: &str,
    filename: PathBuf,
) -> PyResult<(
    HashMap<Version, Vec<pypi_releases::File>>,
    Vec<String>,
    Vec<String>,
)> {
    let mut releases: HashMap<Version, Vec<pypi_releases::File>> = HashMap::new();
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
                Ok(version) => releases.entry(version.clone()).or_default().push(file),
                Err(_) => invalid_versions.push(version),
            }
        } else {
            ignored_filenames.push(file.filename.clone());
        }
    }

    Ok((releases, ignored_filenames, invalid_versions))
}

/// Depth-first recursive iteration over a MarkerTree to collect all marker mappings.
///
/// Ignores everything that doesn't look like `extras = ...`, `extras != ...`, `... = extras` or
/// `... != extras`
pub fn collect_extras_marker_tree(markers: &MarkerTree, mapping: &mut HashSet<String>) {
    match markers {
        MarkerTree::Expression(expression) => {
            match (
                &expression.l_value,
                &expression.operator,
                &expression.r_value,
            ) {
                (
                    MarkerValue::Extra,
                    MarkerOperator::Equal | MarkerOperator::NotEqual,
                    MarkerValue::QuotedString(extra),
                )
                | (
                    MarkerValue::QuotedString(extra),
                    MarkerOperator::Equal | MarkerOperator::NotEqual,
                    MarkerValue::Extra,
                ) => {
                    mapping.insert(extra.to_string());
                }
                _ => {
                    // We ignore all other or weird patterns
                }
            }
        }
        MarkerTree::And(marker_trees) | MarkerTree::Or(marker_trees) => {
            for marker_tree in marker_trees {
                collect_extras_marker_tree(marker_tree, mapping);
            }
        }
    }
}

/// Returns all extras that affect this requirement
///
/// TODO(konstin): Extra name normalization
#[pyfunction]
pub fn collect_extras(requirement: &Requirement) -> HashSet<String> {
    if let Some(markers) = &requirement.marker {
        let mut mapping = HashSet::new();
        collect_extras_marker_tree(markers, &mut mapping);
        mapping
    } else {
        HashSet::new()
    }
}
