# Retired permission CLI

Under #3134, the redundant `permission status` and `permission set` CLI group is
removed. Settings → Permission Mode remains the maintained Worker permission
control through `/api/settings/permission-mode`; `inspect config` provides the
read-only effective configuration. Worker permission semantics, validation and
audit recording are unchanged. Operator permissions and Live unlock are distinct
authorities and are not changed by this retirement.
