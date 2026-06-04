"""On-demand self-upgrade for Aletheia source code.

Human-triggered only (CLI `aletheia update`, dashboard button, /api/update). This
package is intentionally NOT imported by the agent runtime or tool registry, so it
can never be invoked by an LLM-driven agent (enforced by test). It does not run as
a daemon and never auto-restarts the gateway.
"""
