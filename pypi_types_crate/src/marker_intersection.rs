//! Compute whether markers are possible intersecting or definitely disjoint

use crate::normalized_marker_expression::{
    normalize_marker_expression, NormalizedExtraEqualityOperator, NormalizedMarkerEqualityOperator,
    NormalizedMarkerExpression,
};
use crate::version_intersection::is_disjoint_version_specifiers;
use pep508_rs::{MarkerExpression, MarkerTree, MarkerWarningKind};
use std::ops::Deref;

/// Marker representation in [disjunctive normal form (DNF)](https://en.wikipedia.org/wiki/Disjunctive%20normal%20form)
///
/// DNF is an `or` made up of `and`:
/// `(a1 == b1 and a2 == b2 and ...) or (a3 == b3 and a4 == b4 and ..) or ...`
#[derive(Debug, Eq, PartialEq)]
struct MarkerTreeDnf(Vec<Vec<MarkerExpression>>);

impl MarkerTreeDnf {
    /// Build an `And(vec![Or(), Or(), ...])` tree
    fn into_marker_tree(self) -> MarkerTree {
        if self.0.len() == 1 {
            if self.0[0].len() == 1 {
                MarkerTree::Expression(self.0[0][0].clone())
            } else {
                MarkerTree::And(
                    self.0[0]
                        .clone()
                        .into_iter()
                        .map(MarkerTree::Expression)
                        .collect(),
                )
            }
        } else {
            MarkerTree::Or(
                self.0
                    .into_iter()
                    .map(|and| {
                        MarkerTree::And(and.into_iter().map(MarkerTree::Expression).collect())
                    })
                    .collect(),
            )
        }
    }
}

impl Deref for MarkerTreeDnf {
    type Target = Vec<Vec<MarkerExpression>>;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

/// Returns the disjunctive normal form (DNF) as nested vec
///
/// For simplicity, we replace every [MarkerExpression] here with a single letter boolean variable.
///
/// We have the following basic logic operations:
///
/// ```text
/// (A or B) or C -> A or B or C
/// (A or B) and C -> (A and C) or (B and C)
/// (A and B) or C -> (A and B) or C
/// (A and B) and C -> A and B and C
/// ```
///
/// We recursively produce DNF for each element. For the `or` case we use the associativity of `or`
/// to remove the parentheses/flatten the vec. For the `and` case, we use the distributive property
/// of `and` and `or` to "multiply" everything:
///
/// ```
/// (A or B) and (C or D) => (A and C) or (A and D) or (B and C) or (B and D)
/// ```
///
/// which we can generalize to
///
/// ```
/// ((A1 and A2 and ...) or (B1 and B2 and ...) or ...) and ((C1 and C2 and ...) or (D1 and D2 and ...) or ...)
/// => (A1 and A2 and ... and C1 and C2 and ...) or (A1 and A2 and ... and D1 and D2 and ...) or \
/// (B1 and B2 and ... and C1 and C2 and ...) or (B1 and B2 and ... and D1 and D2 and ...) or ...
/// ```
///
/// Or more formally ([sorry](https://github.com/rust-lang/rust/issues/34261)):
///
/// ```
/// (∨_{i} ∧_{j ∈ X_j} X_{i,j}) ∧ (∨_{k} ∧_{l ∈ Y_k} Y_{k,l}) ⇒ (∨_{i} ∨_{k} ((∧_{j ∈ X_j} X_{i,j}) ∧ (∧_{l ∈ Y_k} Y_{k,l} )))
/// ```
///
/// We eliminate redundant clauses: Each clause is canonicalized by sorting its members and we only
/// add a clause to an `or` or `and` vec if it is not already in the list.
fn marker_dnf_as_vec(
    marker: MarkerTree,
    reporter: &mut impl FnMut(MarkerWarningKind, String, &MarkerExpression),
) -> MarkerTreeDnf {
    match marker {
        MarkerTree::Expression(expr) => MarkerTreeDnf(vec![vec![expr]]),
        MarkerTree::And(and) => {
            let mut current = Vec::new();
            // We progressively fold each clause in our and chain into the DNF.
            // We essentially flatten the following, in each step pushing one more line on our
            // accumulator
            // (
            //  ((A and B and ...) or (C and D and ...) or ...) and
            //  ((E and F and ...) or (G and H and ...) or ...) and
            //  ((I and J and ...) or (K and L and ...) or ...) and
            //  ....
            // )
            for entry in and {
                let entry_dnf = marker_dnf_as_vec(entry, reporter).0;
                if current.is_empty() {
                    // We just converted the first clause to DNF, so that's now our new current DNF
                    current = entry_dnf;
                    continue;
                }
                let mut next = Vec::with_capacity(current.len() + entry_dnf.len());
                for left_or in current {
                    for right_or in entry_dnf.clone() {
                        // Combine the two and sets while removing duplicates
                        let mut and_accumulator =
                            Vec::with_capacity(left_or.len() + right_or.len());
                        for left_and in left_or.clone() {
                            if !and_accumulator.contains(&left_and) {
                                and_accumulator.push(left_and);
                            }
                        }
                        for right_and in right_or {
                            if !and_accumulator.contains(&right_and) {
                                and_accumulator.push(right_and);
                            }
                        }
                        // TODO(konstin): Implement a lexicographic sort function in
                        // MarkerExpression itself, and use here and in the two cases below
                        and_accumulator.sort_by_key(|entry| entry.to_string());

                        // Is the any pair marker we connected by `and` that are contradictory
                        // to each other? In that case the whole `and` clause resolves to false
                        // and we don't add it to the list of `or`.
                        let is_false_clause = and_accumulator.iter().any(|left_marker| {
                            and_accumulator.iter().any(|right_marker| {
                                is_disjoint_marker_expression(left_marker, right_marker, reporter)
                            })
                        });
                        if is_false_clause {
                            continue;
                        }

                        if !next.contains(&and_accumulator) {
                            next.push(and_accumulator);
                        }
                    }
                }
                current = next;
            }
            // Use better sort here too
            current.sort_by_key(|entry| (entry.len(), format!("{:?}", entry)));
            MarkerTreeDnf(current)
        }
        MarkerTree::Or(or) => {
            // This case is easy, we just need to flatten the nested or list
            // (A or B or ...) or (C or D or ...) => (A or B or ... or C or ... D or ...)
            // and remove redundancy.
            let mut flattened = Vec::new();
            for entry in or {
                let dnf = marker_dnf_as_vec(entry, reporter);
                for dnf_and in dnf.0 {
                    if !flattened.contains(&dnf_and) {
                        flattened.push(dnf_and);
                    }
                }
            }
            // Use better sort here too
            flattened.sort_by_key(|entry| (entry.len(), format!("{:?}", entry)));
            MarkerTreeDnf(flattened)
        }
    }
}

/// For the resolver, we want to know whether the two markers of two requirements for the same
/// package with different version specifiers are mutually exclusive. If so, we will resolver
/// independently solve for both cases.
///
/// We compute the DNF of (left and right). If we get an empty set, that means each conjunction from
/// left is contradictory with each conjunction from right and we return true.
///
/// To illustrate let left be
/// (A and B) or (C and D)
/// , let right be
/// (not A and not C) or (not B and not D)
/// . Together we get:
/// ((A and B) or (C and D)) and ((not A and not C) or (not B and not D))
/// (A and B and not A and not C) or (A and B and not B and not D) or (C and D and not A and not C) or (C and D and not B and not D)
/// false or false or false or false
/// {}
/// i.e. if the outer disjunction is empty the whole DNF is false
pub fn is_disjoint_marker_tree(
    left: &MarkerTree,
    right: &MarkerTree,
    reporter: &mut impl FnMut(MarkerWarningKind, String, &MarkerExpression),
) -> bool {
    marker_dnf_as_vec(MarkerTree::And(vec![left.clone(), right.clone()]), reporter).is_empty()
}

pub fn is_disjoint_marker_expression(
    left: &MarkerExpression,
    right: &MarkerExpression,
    reporter: &mut impl FnMut(MarkerWarningKind, String, &MarkerExpression),
) -> bool {
    let (left, right) = if let (Some(left), Some(right)) = (
        normalize_marker_expression(left, reporter),
        normalize_marker_expression(right, reporter),
    ) {
        (left, right)
    } else {
        // If the marker is soft-invalid, we can't truly determine whether it is disjoint with anything else
        return false;
    };

    match (left, right) {
        (
            NormalizedMarkerExpression::MarkerEnvVersion {
                field: left_field,
                version_specifiers: left_version_specifiers,
            },
            NormalizedMarkerExpression::MarkerEnvVersion {
                field: right_field,
                version_specifiers: right_version_specifiers,
            },
        ) => {
            left_field == right_field
                && is_disjoint_version_specifiers(
                    &left_version_specifiers,
                    &right_version_specifiers,
                )
        }
        (
            NormalizedMarkerExpression::MarkerEnvString {
                marker_value: left_marker_value,
                operator: left_operator,
            },
            NormalizedMarkerExpression::MarkerEnvString {
                marker_value: right_marker_value,
                operator: right_operator,
            },
        ) => {
            // e.g. `os_name == "A"` and `sys_platform == "B"` are always overlapping
            if left_marker_value.get_marker() != right_marker_value.get_marker() {
                return false;
            }

            // We will only check for `!=` vs `==` and `==` with two different values,
            // not `in`/`not in` or lexicographic
            // Case 1: `os_name == "posix"` vs `"os_name == "nt"`
            if left_operator == NormalizedMarkerEqualityOperator::Equal
                && right_operator == NormalizedMarkerEqualityOperator::Equal
                && left_marker_value.get_value() != right_marker_value.get_value()
            {
                return true;
            }

            // Case 2: `platform_machine == "x86_64"` vs `platform_machine != "x86_64"`
            let disjoint_operators = (left_operator == NormalizedMarkerEqualityOperator::NotEqual
                && right_operator == NormalizedMarkerEqualityOperator::Equal)
                || (left_operator == NormalizedMarkerEqualityOperator::Equal
                    && right_operator == NormalizedMarkerEqualityOperator::NotEqual);
            // look for `sys_platform != "darwin"` vs `sys_platform == "darwin"`
            if left_marker_value.get_marker() == right_marker_value.get_marker()
                && left_marker_value.get_value() == right_marker_value.get_value()
                && disjoint_operators
            {
                return true;
            }

            false
        }
        (
            NormalizedMarkerExpression::Extra {
                operator: left_operator,
                value: left_value,
            },
            NormalizedMarkerExpression::Extra {
                operator: right_operator,
                value: right_value,
            },
        ) => {
            let disjoint_operators = (left_operator == NormalizedExtraEqualityOperator::NotEqual
                && right_operator == NormalizedExtraEqualityOperator::Equal)
                || (left_operator == NormalizedExtraEqualityOperator::Equal
                    && right_operator == NormalizedExtraEqualityOperator::NotEqual);
            // look for `extra != "native"` vs `extra == "native"`
            left_value == right_value && disjoint_operators
        }
        // We have different marker types, they can't be mutually exclusive
        (_, _) => false,
    }
}

#[cfg(test)]
mod test {
    use super::marker_dnf_as_vec;
    use crate::marker_intersection::is_disjoint_marker_tree;
    use pep508_rs::{MarkerExpression, MarkerTree, MarkerWarningKind};
    use std::collections::HashSet;
    use std::str::FromStr;

    fn no_reports(kind: MarkerWarningKind, message: String, expression: &MarkerExpression) {
        panic!(
            "There shouldn't have been this warning: {:?} {} {}",
            kind, message, expression
        )
    }

    fn dnf(marker: &str) -> MarkerTree {
        marker_dnf_as_vec(MarkerTree::from_str(marker).unwrap(), &mut no_reports).into_marker_tree()
    }

    fn assert_dnf_equal(left: &str, right: &str) {
        assert_eq!(dnf(left).to_string(), right);
    }

    /// SQLAlchemy/1.4.47
    #[test]
    fn test_dnf_sqlalchemy() {
        let marker = r#"python_version >= "3" and 
            (platform_machine == "aarch64" or 
            (platform_machine == "ppc64le" or 
            (platform_machine == "x86_64" or 
            (platform_machine == "amd64" or 
            (platform_machine == "AMD64" or 
            (platform_machine == "win32" or platform_machine == "WIN32")
            )))))"#;
        let expected = "(platform_machine == 'AMD64' and python_version >= '3') or \
            (platform_machine == 'WIN32' and python_version >= '3') or \
            (platform_machine == 'aarch64' and python_version >= '3') or \
            (platform_machine == 'amd64' and python_version >= '3') or \
            (platform_machine == 'ppc64le' and python_version >= '3') or \
            (platform_machine == 'win32' and python_version >= '3') or \
            (platform_machine == 'x86_64' and python_version >= '3')";
        assert_dnf_equal(marker, expected);
    }

    #[test]
    fn test_grpcio() {
        let markers = [
            r#"python_version < "3.10" and sys_platform != "darwin""#,
            r#"python_version < "3.10" and sys_platform == "darwin""#,
            r#"python_version >= "3.10" and sys_platform != "darwin""#,
            r#"python_version >= "3.10" and sys_platform == "darwin""#,
        ];
        for left in markers {
            for right in markers {
                if left == right {
                    continue;
                }

                let left = MarkerTree::from_str(left).unwrap();
                let right = MarkerTree::from_str(right).unwrap();
                assert!(is_disjoint_marker_tree(&left, &right, &mut no_reports));
            }
        }
    }

    #[test]
    fn test_dnf_synthetic() {
        let marker = r#"(os_name == "A" or platform_machine == "B" or platform_version == "C") and
            (os_name == "A" or sys_platform == "E" or platform_machine == "F") and 
            (os_name == "B")"#;
        let expected = "(os_name == 'B' and platform_machine == 'B' and sys_platform == 'E') or \
            (os_name == 'B' and platform_machine == 'F' and platform_version == 'C') or \
            (os_name == 'B' and platform_version == 'C' and sys_platform == 'E')";
        assert_dnf_equal(marker, expected);
    }

    #[test]
    fn test_duplicate_elimination() {
        // Check elimination of identical clauses and conjunctions
        let marker = r#"(os_name == "A" or os_name == "A" or os_name == "A") and 
            (os_name == "A" or os_name == "A")"#;
        let expected = "os_name == 'A'";
        assert_dnf_equal(marker, expected);
    }

    #[test]
    fn test_contradiction_elimination() {
        // Tests both cases, different string values and equals vs not equal
        let markers = [
            r#"platform_machine == "x86_64" and platform_machine != "x86_64""#,
            r#"os_name == "posix" and os_name == "nt""#,
        ];
        for marker in markers {
            assert_dnf_equal(marker, "");
        }
    }

    /// This case is special because we want to actually change the marker
    #[test]
    fn test_intersecting_identical_python_stable() {
        let marker = r#"python_version == "3.8" and python_version == "3.8""#;
        let expected = r#"python_version == '3.8'"#;
        assert_dnf_equal(marker, expected);
    }

    #[test]
    fn test_disjoint_python_stable() {
        let marker = r#"python_version == "3.8" and python_version != "3.8""#;
        assert_dnf_equal(marker, "");
    }

    /// TODO(konstin): emit every warning only once
    #[test]
    fn test_warnings() {
        // Something complex for the DNF and something dubious
        let marker = r#"(os_name == "A" or platform_machine == "B") and
            (os_name == "B" or sys_platform == "E") and 
            ("3.7" in python_full_version) and 
            (os_name == "B" or platform_system == "Java")"#;
        let marker = MarkerTree::from_str(marker).unwrap();
        let mut warnings = HashSet::new();
        marker_dnf_as_vec(marker, &mut |kind, message, expression| {
            warnings.insert((kind, message, expression.clone()));
        });
        let warning = (MarkerWarningKind::Pep440Error,
                 "Expected PEP 440 version operator to compare '3.7' with python_full_version, found 'in'".to_string(),
                 MarkerExpression::from_str(r#""3.7" in python_full_version"#).unwrap());
        assert_eq!(warnings.into_iter().collect::<Vec<_>>(), [warning]);
    }
}
