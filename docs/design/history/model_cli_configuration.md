# Retired model CLI configuration

Under #3134, `model set` is removed. It used fixed transport names as endpoint
identities and directly mutated provider configuration, default selection and
credential references. Settings owns endpoint profile configuration and its
verification path. CLI status, provider listing, connectivity tests and bounded
inference probes remain diagnostics; no configuration forwarding alias remains.
