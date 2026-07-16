// Keep the release-selection/process host in its focused source file while exposing
// bootstrap and update transactions as first-class library modules. `include!` preserves
// the existing crate-root paths and tests without creating a second host implementation.
include!("lib.rs");

pub mod bootstrap;
pub mod update;

pub use bootstrap::ensure_bootstrapped;
