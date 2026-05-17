# Issues

Audit trail of bugs. Each entry: date, area, description, root cause (**code bug** vs **test bug**), status (Open / Fixed) + commit on resolution.

| Date | Area | Description | Root cause | Status | Resolved by |
| --- | --- | --- | --- | --- | --- |
| 2026-05-17 | Frontend (transactions) | Slot ID `-1` renders literally as `S-1`. ESPN uses `-1` for "no slot" (the player wasn't in a lineup before/after). | Code bug — `app.js:txItemLine` concatenates `"S" + slot` without filtering sentinel values. | Fixed | (this commit) |
| 2026-05-17 | Frontend (transactions) | Team ID `0` renders as `T0`. ESPN uses `0` for "no team" (free-agent pool / not a team-change). | Code bug — same function. | Fixed | (this commit) |
| 2026-05-17 | Frontend (transactions) | Slot IDs rendered as raw `S1` / `S6` instead of position labels (G / UTIL / BE). | Code bug — no slot label map in JS. | Fixed | (this commit) |
