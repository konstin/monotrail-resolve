//! Determine whether VersionSpecifiers intersect, i.e. if there exists any version they both match
//!
//! We use the notation from https://en.wikipedia.org/wiki/Interval_(mathematics)#Terminology for
//! intervals.

use pep440_rs::{Version, VersionSpecifier, VersionSpecifiers};
use std::cmp::Ordering;
use std::fmt::{Display, Formatter};

/// True if there is no version that would satisfy both specifier
pub fn is_disjoint_version_specifier(left: &VersionSpecifier, right: &VersionSpecifier) -> bool {
    let (left1, left2) = version_specifier_to_ranges(left);
    let (right1, right2) = version_specifier_to_ranges(right);

    // Test all four combinations of possibly overlapping intervals
    if !left1.is_disjoint(&right1) {
        return false;
    }
    if let Some(left2) = &left2 {
        if !left2.is_disjoint(&right1) {
            return false;
        }
    }
    if let Some(right2) = &right2 {
        if !right2.is_disjoint(&left1) {
            return false;
        }
    }
    if let (Some(left2), Some(right2)) = (&left2, &right2) {
        if !left2.is_disjoint(right2) {
            return false;
        }
    }

    true
}

fn version_specifiers_to_ranges(specifiers: &VersionSpecifiers) -> Vec<VersionRange> {
    let all_versions = VersionRange {
        min: None,
        min_inclusive: true,
        max: None,
        max_inclusive: true,
    };
    // An empty specifier allows all versions
    let mut left_merged = vec![all_versions];
    for left_specifier in specifiers.iter() {
        // TODO(konstin): Change the datastructure again
        let mut new_merged = Vec::new();
        let (range1, range2) = version_specifier_to_ranges(left_specifier);
        for existing in left_merged {
            if !existing.is_disjoint(&range1) {
                new_merged.push(existing.intersect(&range1));
            }
            if let Some(range2) = &range2 {
                if !existing.is_disjoint(range2) {
                    new_merged.push(existing.intersect(range2));
                }
            }
        }
        left_merged = new_merged;
    }
    left_merged
}

/// True if any two specifier in left and right are mutually exclusive
pub fn is_disjoint_version_specifiers(left: &VersionSpecifiers, right: &VersionSpecifiers) -> bool {
    let left_merged = version_specifiers_to_ranges(left);
    let right_merged = version_specifiers_to_ranges(right);

    // Check if there exists any range that have an overlap where there exists a shared version
    for left_range in &left_merged {
        for right_range in &right_merged {
            if !left_range.is_disjoint(right_range) {
                return false;
            }
        }
    }
    true
}

/// Intermediary that we translate a [VersionSpecifier] to
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VersionRange {
    /// None is -inf
    min: Option<Version>,
    min_inclusive: bool,
    /// None is inf
    max: Option<Version>,
    max_inclusive: bool,
}

impl Display for VersionRange {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        if self.min_inclusive {
            f.write_str("[")?;
        } else {
            f.write_str("(")?;
        }
        if let Some(min) = &self.min {
            write!(f, "{}", min)?;
        } else {
            f.write_str("-inf")?;
        }
        f.write_str(", ")?;
        if let Some(max) = &self.max {
            write!(f, "{}", max)?;
        } else {
            f.write_str("inf")?;
        }
        if self.max_inclusive {
            f.write_str("]")?;
        } else {
            f.write_str(")")?;
        }
        Ok(())
    }
}

impl VersionRange {
    /// Returns the merged range from two overlapping ranges, assuming they do overlap
    #[allow(unused)] // let's keep this for now, might come in handy later
    fn merge(&self, other: &Self) -> Self {
        let (min, min_inclusive) = match self.min.cmp(&other.min) {
            Ordering::Less => (self.min.clone(), self.min_inclusive),
            Ordering::Equal => (self.min.clone(), self.min_inclusive || other.min_inclusive),
            Ordering::Greater => (other.min.clone(), other.min_inclusive),
        };
        let (max, max_inclusive) = match self.max.cmp(&other.max) {
            Ordering::Greater => (self.max.clone(), self.max_inclusive),
            Ordering::Equal => (self.max.clone(), self.max_inclusive || other.max_inclusive),
            Ordering::Less => (other.max.clone(), other.max_inclusive),
        };
        Self {
            min,
            min_inclusive,
            max,
            max_inclusive,
        }
    }

    /// Returns the version range covered by both ranges, assuming they do overlap
    fn intersect(&self, other: &Self) -> Self {
        // for min None < Some(_) has the right semantic
        let (min, min_inclusive) = match self.min.cmp(&other.min) {
            Ordering::Greater => (self.min.clone(), self.min_inclusive),
            Ordering::Equal => (self.min.clone(), self.min_inclusive && other.min_inclusive),
            Ordering::Less => (other.min.clone(), other.min_inclusive),
        };
        // for max None < Some(_) is the inverse if what we want to do
        let (max, max_inclusive) = match (&self.max, &other.max) {
            (None, None) => (None, true),
            (None, _) => (other.max.clone(), other.max_inclusive),
            (_, None) => (self.max.clone(), self.max_inclusive),
            (Some(_), Some(_)) => match self.max.cmp(&other.max) {
                Ordering::Less => (self.max.clone(), self.max_inclusive),
                Ordering::Equal => (self.max.clone(), self.max_inclusive && other.max_inclusive),
                Ordering::Greater => (other.max.clone(), other.max_inclusive),
            },
        };

        Self {
            min,
            min_inclusive,
            max,
            max_inclusive,
        }
    }

    fn is_disjoint(&self, other: &Self) -> bool {
        // .min            .max
        // |+++left++++++++|
        //             |+++other+++++|
        //             .min          .max
        let overlapping1 = self
            .max
            .as_ref()
            .zip(other.min.as_ref())
            .map_or(true, |(left_max, right_min)| left_max > right_min)
            || (self.max == other.min && self.max_inclusive && other.min_inclusive);

        //             .min            .max
        //             |+++self++++++++|
        // |+++other+++++|
        // .min          .max
        let overlapping2 = other
            .max
            .as_ref()
            .zip(self.min.as_ref())
            .map_or(true, |(right_max, left_min)| right_max > left_min)
            || (self.min == other.max && self.min_inclusive && other.max_inclusive);
        // We determined if the intervals are overlapping, so now invert
        !(overlapping1 && overlapping2)
    }
}

fn version_specifier_to_ranges(
    specifier: &VersionSpecifier,
) -> (VersionRange, Option<VersionRange>) {
    match specifier.operator() {
        pep440_rs::Operator::Equal => (
            VersionRange {
                min: Some(specifier.version().clone()),
                min_inclusive: true,
                max: Some(specifier.version().clone()),
                max_inclusive: true,
            },
            None,
        ),
        pep440_rs::Operator::EqualStar => {
            // VersionSpecifier guarantees that at least one number in release exists
            let mut max = specifier.version().clone();
            *max.release.last_mut().unwrap() += 1;

            (
                VersionRange {
                    min: Some(specifier.version().clone()),
                    min_inclusive: true,
                    max: Some(max),
                    max_inclusive: false,
                },
                None,
            )
        }
        pep440_rs::Operator::ExactEqual => (
            VersionRange {
                min: Some(specifier.version().clone()),
                min_inclusive: true,
                max: Some(specifier.version().clone()),
                max_inclusive: true,
            },
            None,
        ),
        pep440_rs::Operator::NotEqual => (
            VersionRange {
                min: None,
                min_inclusive: true,
                max: Some(specifier.version().clone()),
                max_inclusive: false,
            },
            Some(VersionRange {
                min: Some(specifier.version().clone()),
                min_inclusive: false,
                max: None,
                max_inclusive: true,
            }),
        ),
        pep440_rs::Operator::NotEqualStar => {
            let mut larger = specifier.version().clone();
            *larger.release.last_mut().unwrap() += 1;

            (
                VersionRange {
                    min: None,
                    min_inclusive: true,
                    max: Some(specifier.version().clone()),
                    max_inclusive: false,
                },
                Some(VersionRange {
                    min: Some(larger),
                    min_inclusive: true,
                    max: None,
                    max_inclusive: true,
                }),
            )
        }
        pep440_rs::Operator::TildeEqual => {
            // VersionSpecifier guarantees that version has a least two release numbers.
            // Transform e.g. `1.2.3.8` to `1.2.4`, which gives us the exclusive upper bound of ~=
            let mut max = specifier.version().clone();
            max.release.pop();
            *max.release
                .last_mut()
                .expect("Invalid VersionSpecifier that must not exist") += 1;

            (
                VersionRange {
                    min: Some(specifier.version().clone()),
                    min_inclusive: true,
                    max: Some(max),
                    max_inclusive: false,
                },
                None,
            )
        }
        pep440_rs::Operator::LessThan => (
            VersionRange {
                min: None,
                min_inclusive: true,
                max: Some(specifier.version().clone()),
                max_inclusive: false,
            },
            None,
        ),
        pep440_rs::Operator::LessThanEqual => (
            VersionRange {
                min: None,
                min_inclusive: true,
                max: Some(specifier.version().clone()),
                max_inclusive: true,
            },
            None,
        ),
        pep440_rs::Operator::GreaterThan => (
            VersionRange {
                min: Some(specifier.version().clone()),
                min_inclusive: false,
                max: None,
                max_inclusive: true,
            },
            None,
        ),
        pep440_rs::Operator::GreaterThanEqual => (
            VersionRange {
                min: Some(specifier.version().clone()),
                min_inclusive: true,
                max: None,
                max_inclusive: true,
            },
            None,
        ),
    }
}

#[cfg(test)]
mod test {
    use crate::version_intersection::{
        is_disjoint_version_specifier, is_disjoint_version_specifiers, version_specifiers_to_ranges,
    };
    use pep440_rs::{VersionSpecifier, VersionSpecifiers};
    use std::str::FromStr;

    #[test]
    fn test_disjoint_python_stable() {
        let disjoint = vec![
            (">= 3.8", "< 3.8"),
            (">= 3.8", "< 3.7"),
            ("> 3.8", "<= 3.8"),
            ("> 3.8", "<= 3.7"),
            ("== 3.8", "!= 3.8"),
            ("== 3.8.*", ">= 3.9"),
            ("== 3.8.*", "< 3.8"),
        ];
        for (left, right) in disjoint {
            let left = VersionSpecifier::from_str(left).unwrap();
            let right = VersionSpecifier::from_str(right).unwrap();
            assert!(is_disjoint_version_specifier(&left, &right));
        }
    }

    #[test]
    fn test_disjoint_python_postfix() {
        let disjoint = vec![
            (">= 3.8b1", "< 3.8b1"),
            (">= 3.8b1.post1", "< 3.8b1.post1"),
            (">= 3.8.post1", "< 3.8.post1"),
        ];
        for (left, right) in disjoint {
            let left = VersionSpecifier::from_str(left).unwrap();
            let right = VersionSpecifier::from_str(right).unwrap();
            assert!(is_disjoint_version_specifier(&left, &right));
        }
    }

    #[test]
    fn test_intersecting_python_stable() {
        let intersecting = vec![
            ("== 3.8", "== 3.8.*"),
            ("<= 3.9", "> 3.8"),
            ("< 3.9", ">= 3.8"),
            ("<= 3.9", ">= 3.8"),
            ("<= 3.8", ">= 3.8"),
            ("== 3.8.*", "> 3.8"),
            ("== 3.8.*", ">= 3.8"),
        ];
        for (left, right) in intersecting {
            let left = VersionSpecifier::from_str(left).unwrap();
            let right = VersionSpecifier::from_str(right).unwrap();
            assert!(!is_disjoint_version_specifier(&left, &right));
        }
    }

    #[test]
    fn test_intersecting_python_postfix() {
        let intersecting = vec![
            ("== 3.8.*", "> 3.8b1"),
            ("== 3.8.*", "> 3.8.post1"),
            ("< 3.8.1b1", "== 3.8.*"),
            ("< 3.8.post1", "== 3.8.*"),
        ];
        for (left, right) in intersecting {
            let left = VersionSpecifier::from_str(left).unwrap();
            let right = VersionSpecifier::from_str(right).unwrap();
            assert!(!is_disjoint_version_specifier(&left, &right));
        }
    }

    #[test]
    fn test_intersecting_version_specifiers() {
        let left = VersionSpecifiers::from_str(">= 3.7, != 3.8.0, < 3.11").unwrap();
        let right = VersionSpecifiers::from_str("> 3.10, < 3.12").unwrap();
        assert!(!is_disjoint_version_specifiers(&left, &right));
    }

    #[test]
    fn test_disjoint_version_specifiers() {
        let left = VersionSpecifiers::from_str("== 3.10.*, != 3.10.2").unwrap();
        let right = VersionSpecifiers::from_str(">= 3.11, < 3.12").unwrap();
        assert!(is_disjoint_version_specifiers(&left, &right));
    }

    #[test]
    fn test_disjoint_version_specifiers_empty() {
        let left = VersionSpecifiers::from_str("> 1").unwrap();
        let right = VersionSpecifiers::from_str("< 1, > 2").unwrap();
        assert!(is_disjoint_version_specifiers(&left, &right));
    }

    #[test]
    fn test_bounds() {
        let specifiers = [
            (">= 3.7, != 3.8.0, < 3.11", "[3.7, 3.8.0) (3.8.0, 3.11)"),
            ("> 3.10, < 3.12", "(3.10, 3.12)"),
            ("== 3.10.*, != 3.10.2", "[3.10, 3.10.2) (3.10.2, 3.11)"),
            (">=3.11, <3.12", "[3.11, 3.12)"),
            ("<1, >2", ""),
        ];

        for (specifiers, expected) in specifiers {
            let specifiers = VersionSpecifiers::from_str(specifiers).unwrap();
            let actual = version_specifiers_to_ranges(&specifiers)
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(" ");
            assert_eq!(actual, expected);
        }
    }
}
