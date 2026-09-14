# Retired secrets CLI read surfaces

Under #3134, `secrets list` no longer enumerates a fixed cross-service list of
stored credentials. Configured endpoint status belongs to Settings and the
existing scoped diagnostics. `secrets test` duplicated `bitget test` and is
removed without forwarding. `secrets set` remains for explicit setup, including
the current Telegram token setup path; its prompt, audit and Live rotation guard
are unchanged. This retirement does not claim full Core credential isolation.
