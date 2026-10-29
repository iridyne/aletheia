## D0: restore valid Bitget classic funding-history request bounds

Related #3134 / #3135. Contract Delta: NONE. Fedora exact03fa69c93 boots correctly but two historical closed Demo Positions remain funding-incomplete. The existing essential settler ran under the original manual brake and failed; read-only diagnosis traced ValueError to private_funding_ledger_unavailable, then the original CCXT BadRequest/venue40020. No identities, fees, history, Risk, Live, credentials or qualification windows were changed.

Root: CCXTUTAAdapter default query page_limit1000 reaches BitgetClassicV2Profile.fetch_funding_rows unchanged. Current installed CCXT forwards that limit directly to GET /api/v2/mix/account/bill. Bitget official classic endpoint permits at most100. Same persisted Position/account/window, independent read-only diagnostic changing only request page size to100 returned one funding event; no settlement/materialization was called. This is a real deployed request defect, not a cosmetic #3134 refactor.

Authority: https://www.bitget.com/zh-CN/api-doc/classic/contract/account/Get-Account-Bill

Smallest repair: cap the Bitget profile's actual single-page request to the documented maximum, and judge saturation against that actual requested size. A full page remains PARTIAL/funding_history_bound_reached, never silently COMPLETE against the old1000 threshold. Preserve smaller caller limits. No new paginator/framework, account/history rewrite, zero-cost fabrication, manual close or expanded credential authority.

Acceptance: focused deterministic request-bound and saturated-page regressions, existing UTA/funding-settlement tests, fresh review and ordinary bounded PR. Then exact merged-head deployment with original brakes; existing scheduler alone may materialize verified economics. Old recovered closes do not count as new-build natural Demo qualification evidence.
