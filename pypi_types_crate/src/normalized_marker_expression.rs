use pep440_rs::{Version, VersionSpecifier, VersionSpecifiers};
use pep508_rs::{
    MarkerExpression, MarkerOperator, MarkerValue, MarkerValueString, MarkerValueVersion,
    MarkerWarningKind,
};
use std::str::FromStr;

/// `!=` or `==`
#[derive(Eq, PartialEq)]
pub enum NormalizedExtraEqualityOperator {
    /// `==`
    Equal,
    /// `!=`
    NotEqual,
}

impl NormalizedExtraEqualityOperator {
    fn from_marker(
        marker: &MarkerExpression,
        reporter: &mut impl FnMut(MarkerWarningKind, String, &MarkerExpression),
    ) -> Option<Self> {
        Some(match marker.operator {
            MarkerOperator::Equal => NormalizedExtraEqualityOperator::Equal,
            MarkerOperator::NotEqual => NormalizedExtraEqualityOperator::NotEqual,
            _ => {
                reporter(
                    MarkerWarningKind::ExtraInvalidComparison,
                    "Comparing extra with something other than equal (`==`) or unequal (`!=`) is \
                    wrong"
                        .to_string(),
                    marker,
                );
                return None;
            }
        })
    }
}

#[derive(Eq, PartialEq)]
pub enum NormalizedMarkerEqualityOperator {
    Equal,
    NotEqual,
    /// Discourage, a warning was raised
    GreaterThan,
    /// Discourage, a warning was raised
    GreaterEqual,
    /// Discourage, a warning was raised
    LessThan,
    /// Discourage, a warning was raised
    LessEqual,
    In,
    NotIn,
}

impl NormalizedMarkerEqualityOperator {
    fn from_marker(
        marker: &MarkerExpression,
        l_string: &MarkerValueString,
        r_string: &str,
        reporter: &mut impl FnMut(MarkerWarningKind, String, &MarkerExpression),
    ) -> Option<Self> {
        Some(match marker.operator {
            MarkerOperator::Equal => Self::Equal,
            MarkerOperator::NotEqual => Self::NotEqual,
            MarkerOperator::GreaterThan => {
                reporter(
                    MarkerWarningKind::LexicographicComparison,
                    format!("Comparing {} and {} lexicographically", l_string, r_string),
                    marker,
                );
                Self::GreaterThan
            }
            MarkerOperator::GreaterEqual => {
                reporter(
                    MarkerWarningKind::LexicographicComparison,
                    format!("Comparing {} and {} lexicographically", l_string, r_string),
                    marker,
                );
                Self::GreaterEqual
            }
            MarkerOperator::LessThan => {
                reporter(
                    MarkerWarningKind::LexicographicComparison,
                    format!("Comparing {} and {} lexicographically", l_string, r_string),
                    marker,
                );
                Self::LessThan
            }
            MarkerOperator::LessEqual => {
                reporter(
                    MarkerWarningKind::LexicographicComparison,
                    format!("Comparing {} and {} lexicographically", l_string, r_string),
                    marker,
                );
                Self::LessEqual
            }
            MarkerOperator::TildeEqual => {
                reporter(
                    MarkerWarningKind::LexicographicComparison,
                    format!("Can't compare {} and {} with `~=`", l_string, r_string),
                    marker,
                );
                return None;
            }
            MarkerOperator::In => Self::In,
            MarkerOperator::NotIn => Self::NotIn,
        })
    }
}

/// We can have both the marker left and the value right as well as the value left and the marker
/// right, e.g. `"Ubuntu" in platform_version` and `sys_platform in "linux"`
pub enum NormalizedMarkerFieldValue {
    /// e.g. `sys_platform in "linux"`
    MarkerLeftValueRight {
        left: MarkerValueString,
        right: String,
    },
    /// e.g. `"Ubuntu" in platform_version`
    ValueLeftMarkerRight {
        left: String,
        right: MarkerValueString,
    },
}

impl NormalizedMarkerFieldValue {
    pub fn get_marker(&self) -> &MarkerValueString {
        match self {
            NormalizedMarkerFieldValue::MarkerLeftValueRight { left, .. } => left,
            NormalizedMarkerFieldValue::ValueLeftMarkerRight { right, .. } => right,
        }
    }

    pub fn get_value(&self) -> &str {
        match self {
            NormalizedMarkerFieldValue::MarkerLeftValueRight { right, .. } => right,
            NormalizedMarkerFieldValue::ValueLeftMarkerRight { left, .. } => left,
        }
    }
}

/// Like [MarkerExpression], but only valid marker/op/value combinations are possible
pub enum NormalizedMarkerExpression {
    /// We want to store both `python_version ~= 3.8.0` and `3.8 ~= python_version` as a specifier.
    /// This is hard because there no operator so that we can write `3.8 ~= python_version` as
    /// `python_version <op> 3.8`. Instead, we transform everything into version specifiers with one
    /// or two version specifier inside. For the `python_version <op> <version>` case this is
    /// trivial, for the `<version> <op> python_version` case we flip the operator if possible
    /// (`>`, `>=`, `<`, `<=`), noop (`==`, `!=`), split into a range (`~=`) or error (star
    /// versions).
    MarkerEnvVersion {
        field: MarkerValueVersion,
        version_specifiers: VersionSpecifiers,
    },
    /// e.g. `os_name == "posix"` or `"aarch64" != platform_machine`
    MarkerEnvString {
        /// Switch between marker left, value right and value left, marker right
        marker_value: NormalizedMarkerFieldValue,
        operator: NormalizedMarkerEqualityOperator,
    },
    /// `extra == "test"` or `extra != "dev"`
    Extra {
        operator: NormalizedExtraEqualityOperator,
        value: String,
    },
}

pub fn invert_and_normalize_version_operator(
    operator: &MarkerOperator,
    l_version: &Version,
) -> Option<VersionSpecifiers> {
    let make_specifiers = |operator| {
        // unwrap is safe here because we made sure there is neither a local version
        // nor a star
        let specifier = VersionSpecifier::new(operator, l_version.clone(), false).unwrap();
        VersionSpecifiers::from_iter([specifier])
    };
    Some(match &operator {
        // symmetric -> noop
        MarkerOperator::Equal => make_specifiers(pep440_rs::Operator::Equal),
        MarkerOperator::NotEqual => make_specifiers(pep440_rs::Operator::NotEqual),
        // flippable
        MarkerOperator::GreaterThan => make_specifiers(pep440_rs::Operator::LessThanEqual),
        MarkerOperator::GreaterEqual => make_specifiers(pep440_rs::Operator::LessThan),
        MarkerOperator::LessThan => make_specifiers(pep440_rs::Operator::GreaterThanEqual),
        MarkerOperator::LessEqual => make_specifiers(pep440_rs::Operator::LessThanEqual),
        // complicated
        MarkerOperator::TildeEqual => {
            // Consider `2.3.4 ~= python_full_version`:
            // `2.3.3 ~= 1.3.3` -> false
            // `2.3.3 ~= 2.0.0` -> true
            // `2.3.3 ~= 2.3.3` -> true
            // `2.3.3 ~= 2.3.4` -> false
            // This we can be split into two constraints:
            // `2.3.4 >= python_full_version` and `2 < python_full_version`
            // invert:
            // `python_full_version <= 2.3.4` and `python_full_version > 2`
            // so the specifiers are:
            // `<= 2.3.4, > 2`
            let lower_bound =
                VersionSpecifier::new(pep440_rs::Operator::LessThanEqual, l_version.clone(), false)
                    .expect("TODO");
            // indexing is safe here because the release must always have at least
            // a major version
            let major_version = Version::from_release(vec![l_version.release[0]]);
            let upper_bound =
                VersionSpecifier::new(pep440_rs::Operator::GreaterThan, major_version, false)
                    // unwrapping is safe because we know that `> X` is always valid
                    .unwrap();
            VersionSpecifiers::from_iter([lower_bound, upper_bound])
        }
        // invalid
        MarkerOperator::In | MarkerOperator::NotIn => {
            return None;
        }
    })
}

fn to_pep440_operator(op: &MarkerOperator) -> Option<pep440_rs::Operator> {
    match op {
        MarkerOperator::Equal => Some(pep440_rs::Operator::Equal),
        MarkerOperator::NotEqual => Some(pep440_rs::Operator::NotEqual),
        MarkerOperator::GreaterThan => Some(pep440_rs::Operator::GreaterThan),
        MarkerOperator::GreaterEqual => Some(pep440_rs::Operator::GreaterThanEqual),
        MarkerOperator::LessThan => Some(pep440_rs::Operator::LessThan),
        MarkerOperator::LessEqual => Some(pep440_rs::Operator::LessThanEqual),
        MarkerOperator::TildeEqual => Some(pep440_rs::Operator::TildeEqual),
        MarkerOperator::In => None,
        MarkerOperator::NotIn => None,
    }
}

pub fn normalize_marker_expression(
    marker: &MarkerExpression,
    reporter: &mut impl FnMut(MarkerWarningKind, String, &MarkerExpression),
) -> Option<NormalizedMarkerExpression> {
    Some(match &marker.l_value {
        // The only sound choice for this is `<version key> <version op> <quoted PEP 440 version>`
        MarkerValue::MarkerEnvVersion(l_key) => {
            let value = &marker.r_value;
            let (r_version, r_star) = if let MarkerValue::QuotedString(r_string) = &value {
                match Version::from_str_star(r_string) {
                    Ok((version, star)) => (version, star),
                    Err(err) => {
                        reporter(
                            MarkerWarningKind::Pep440Error,
                            format!(
                                "Expected PEP 440 version to compare with {}, found {}: {}",
                                l_key, marker.r_value, err
                            ),
                            marker,
                        );
                        return None;
                    }
                }
            } else {
                reporter(
                    MarkerWarningKind::Pep440Error,
                    format!(
                        "Expected double quoted PEP 440 version to compare with {}, found {}",
                        l_key, marker.r_value
                    ),
                    marker,
                );
                return None;
            };

            let operator = match to_pep440_operator(&marker.operator) {
                None => {
                    reporter(
                        MarkerWarningKind::Pep440Error,
                        format!(
                            "Expected PEP 440 version operator to compare {} with '{}', found '{}'",
                            l_key, r_version, marker.operator
                        ),
                        marker,
                    );
                    return None;
                }
                Some(operator) => operator,
            };

            let specifier = match VersionSpecifier::new(operator, r_version, r_star) {
                Ok(specifier) => specifier,
                Err(err) => {
                    reporter(
                        MarkerWarningKind::Pep440Error,
                        format!("Invalid operator/version combination: {}", err),
                        marker,
                    );
                    return None;
                }
            };

            NormalizedMarkerExpression::MarkerEnvVersion {
                field: l_key.clone(),
                version_specifiers: VersionSpecifiers::from_iter([specifier]),
            }
        }
        // This is half the same block as above inverted
        MarkerValue::MarkerEnvString(l_key) => {
            let r_string = match &marker.r_value {
                MarkerValue::Extra
                | MarkerValue::MarkerEnvVersion(_)
                | MarkerValue::MarkerEnvString(_) => {
                    reporter(
                        MarkerWarningKind::MarkerMarkerComparison,
                        "Comparing two markers with each other doesn't make any sense".to_string(),
                        marker,
                    );
                    return None;
                }
                MarkerValue::QuotedString(r_string) => r_string,
            };

            NormalizedMarkerExpression::MarkerEnvString {
                marker_value: NormalizedMarkerFieldValue::MarkerLeftValueRight {
                    left: l_key.clone(),
                    right: r_string.clone(),
                },
                operator: NormalizedMarkerEqualityOperator::from_marker(
                    marker, l_key, r_string, reporter,
                )?,
            }
        }
        // `extra == '...'`
        MarkerValue::Extra => {
            let r_value_string = match &marker.r_value {
                MarkerValue::MarkerEnvVersion(_)
                | MarkerValue::MarkerEnvString(_)
                | MarkerValue::Extra => {
                    reporter(
                        MarkerWarningKind::ExtraInvalidComparison,
                        "Comparing extra with something other than a quoted string is wrong"
                            .to_string(),
                        marker,
                    );
                    return None;
                }
                MarkerValue::QuotedString(r_value_string) => r_value_string,
            };
            let operator = NormalizedExtraEqualityOperator::from_marker(marker, reporter)?;
            NormalizedMarkerExpression::Extra {
                operator,
                value: r_value_string.to_string(),
            }
        }
        // This is either MarkerEnvVersion, MarkerEnvString or Extra inverted
        MarkerValue::QuotedString(l_string) => {
            match &marker.r_value {
                // The only sound choice for this is `<quoted PEP 440 version> <version op> <version key> `
                MarkerValue::MarkerEnvVersion(r_key) => {
                    let l_version = match Version::from_str(l_string) {
                        Ok(l_version) => l_version,
                        Err(err) => {
                            reporter(MarkerWarningKind::Pep440Error, format!(
                                "Expected double quoted PEP 440 version to compare with {}, found {}: {}",
                                l_string, marker.r_value, err
                            ), marker);
                            return None;
                        }
                    };

                    if l_version.epoch != 0 {
                        reporter(MarkerWarningKind::Pep440Error, format!(
                            "A PEP 440 version with epoch {} compared with {} will always evaluate to false",
                            l_version.epoch, r_key
                        ), marker);
                        return None;
                    }
                    if l_version.local.is_some() {
                        reporter(MarkerWarningKind::Pep440Error, format!(
                            "A PEP 440 version {} with a local version compared with {} can not be reasonably represented",
                            l_version, r_key
                        ), marker);
                        return None;
                    }

                    let version_specifiers = if let Some(version_specifiers) =
                        invert_and_normalize_version_operator(&marker.operator, &l_version)
                    {
                        version_specifiers
                    } else {
                        reporter(MarkerWarningKind::Pep440Error, format!(
                                "Expected PEP 440 version operator to compare '{}' with {}, found '{}'",
                                l_string, r_key, marker.operator
                            ), marker);
                        return None;
                    };

                    NormalizedMarkerExpression::MarkerEnvVersion {
                        field: r_key.clone(),
                        version_specifiers,
                    }
                }
                // This is half the same block as above inverted
                MarkerValue::MarkerEnvString(r_key) => {
                    NormalizedMarkerExpression::MarkerEnvString {
                        marker_value: NormalizedMarkerFieldValue::ValueLeftMarkerRight {
                            left: l_string.to_string(),
                            right: r_key.clone(),
                        },
                        operator: NormalizedMarkerEqualityOperator::Equal,
                    }
                }
                // `'...' == extra`
                MarkerValue::Extra => NormalizedMarkerExpression::Extra {
                    operator: NormalizedExtraEqualityOperator::from_marker(marker, reporter)?,
                    value: l_string.clone(),
                },
                // `'...' == '...'`, doesn't make much sense
                MarkerValue::QuotedString(_) => {
                    // Not even pypa/packaging 22.0 supports this
                    // https://github.com/pypa/packaging/issues/632
                    reporter(
                        MarkerWarningKind::StringStringComparison,
                        format!(
                            "Comparing two quoted strings with each other doesn't make sense: {}",
                            marker
                        ),
                        marker,
                    );
                    return None;
                }
            }
        }
    })
}
