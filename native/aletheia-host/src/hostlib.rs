// Keep the release-selection/process host in its focused source file while exposing the
// updater transaction as a first-class library module. `include!` preserves the existing
// crate-root paths and tests without creating a second copy of the host implementation.
include!("lib.rs");

pub mod update;
