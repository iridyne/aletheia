# Retired flow CLI alias

Under #3134, the hidden `flow audit` and `flow trace` wrappers were removed.
They delegated to the same read-only implementation now exposed exclusively
as `opportunity audit` and `opportunity trace`. Historical data and audit
reconstruction semantics are unchanged; no runtime forwarding alias remains.

The redundant `alpha audit`, `funnel audit`, and `sizing trace` groups are also
removed under #3134. Their report builders and renderers remain available through
`opportunity audit --view alpha|funnel|sizing`. The canonical CLI expresses windows
in hours (`--hours 168` for seven days); no old command registration is retained.

The hidden `thesis audit` wrapper is likewise removed in favor of the `thesis`
Opportunity view. The separate thesis snapshot/list/record export commands remain.
