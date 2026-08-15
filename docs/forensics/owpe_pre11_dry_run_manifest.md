# OWPE Pre-1.1 Training Compatibility Dry-Run Manifest

## Certification boundary

This is a read-only classification artifact. It performed **zero PostgreSQL writes** and did not apply migration `0046_owpe_pre11_training_compatibility`. It did not mature pending outcomes, create compatibility decisions, create replay labels, rebuild cohorts, or alter any historical snapshot/estimate.

- Training family: `OWPE_1_1_COMPAT_V1`
- Eligibility policy: `owpe-pre11-eligibility-1.0.0`
- Compatibility bridge: `owpe-training-compat-1.0.0`
- Replay policy: `owpe-pre11-replay-1.0.0`
- Replay method: `PRE11_TO_11_TRAINING_REPLAY`
- Target outcome-definition PK: `3`
- Target definition: `T2_5_S2_0_H5_NEXT_OPEN`
- Target calculation/config: `owpe-calc-1.1.0` / `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527`
- Source calculation/config: `owpe-calc-1.0.0` / `2260060ab44d6f46ccff94d61943bbdfcaa49b734ef2ccf177b71dc50f225184`
- Feature schema: `owpe-features-1.0.0` (source and target)
- Training cutoff: `2026-08-14T22:34:33.408271+02:00` (Run 105 source cutoff)
- Prediction-date scope: `2021-08-14` through `2026-08-14`
- Deterministic request key: `d555a6b40f63092527b7997932686342e1be32d0582cbfe5d49f71122c6afa8c`
- Eligible-member manifest hash: `dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54`

## Sequential funnel

| Predicate | After predicate | Removed at predicate |
|---|---:|---:|
| historical snapshots considered | 8,859 | — |
| native snapshots | 8,859 | 0 |
| independently PIT/session valid | 2,854 | 6,005 |
| prediction eligible | 2,845 | 9 |
| immutable source lineage sufficient | 2,845 | 0 |
| feature compatible | 2,845 | 0 |
| config-semantics compatible | 2,845 | 0 |
| active-label replay possible | 715 | 2,130 |
| quality allowed | 715 | 0 |
| independent episode representatives | 390 | 325 |
| inside five-year rolling window | 390 | 0 |
| **final training eligible** | **390** | **0** |

The PIT count is not the stored `point_in_time_validated` Boolean. It recomputes `latest_completed_session(source_data_cutoff_at)`, checks the stored signal date, and verifies `next_regular_session(signal_date)` against the planned entry. This rejects 6,005 snapshots affected by the historical session-date defect.

## First-failure reasons

| Reason code | Count |
|---|---:|
| `PIT_SIGNAL_SESSION_MISMATCH` | 6,005 |
| `ACTIVE_LABEL_REPLAY_NOT_POSSIBLE` | 2,130 |
| `DEPENDENT_EPISODE` | 325 |
| `PREDICTION_NOT_ELIGIBLE` | 9 |

Counts use deterministic first-failure ordering. A row is not counted again under later predicates.

## Candidate L5 composition (not persisted)

| Measure | Dry-run value |
|---|---:|
| sample n | 390 |
| effective n | 390 |
| wins | 145 |
| raw target-first winner rate | 0.3717948718 |
| Beta prior | alpha 10, beta 10 |
| posterior probability | 0.3780487805 |
| lower bound | 0.331112 |
| upper bound | 0.424986 |
| interval width | 0.093874 |
| projected evidence grade | High |
| oldest/newest evidence date | 2026-08-04 / 2026-08-06 |
| native 1.1 n | 0 |
| pre-1.1 compatible/replayed-label n | 390 |

This calculation is only a dry-run proof. No cohort definition, cohort statistic, evidence manifest row, estimate, or member row was written. It is not a production probability.

Eligible members are distributed across source runs 78 (227), 85 (140), and 86 (23). Setup-family composition is Avoid 236, Blocked by earnings gate 39, Candidate 67, Strong candidate 18, and Watchlist 30. All 390 are independent episode representatives. Ranking and sector context are absent for all 390; that excludes them from levels that require those dimensions but does not block L5.

## Pending H5 scope

The prior “remaining 1,111” is reproduced as current pre-1.1 H5 `NEXT_OPEN` rows with status `PENDING` and due session on or before `2026-08-13`.

| Pending-scope predicate | Count |
|---|---:|
| pending H5 total | 1,111 |
| inside rolling window | 1,111 |
| belongs to PIT/lineage/feature/config-compatible candidate snapshot | 337 |
| complete historical five-session bars and replayable active label | 0 |
| final training-eligible/needed by approved scope | 0 |
| not needed for active training | 1,111 |

Therefore the write proposal does **not** drain or mature any of these 1,111 rows.

## Exact eligible prediction IDs

The manifest hash also commits each member's immutable source-manifest hash and five-bar lineage hash. The eligible prediction PKs are:

The 390 members reference 1,950 exact bar positions (1,739 unique `PriceBar` PKs). All have non-null data hashes. Of those positions, 473 use a revised bar and all 473 identify the exact current `PriceBarRevision` PK/revision visible before the cutoff.

`1341, 1342, 1344, 1345, 1348, 1352, 1355, 1356, 1357, 1358, 1359, 1360, 1361, 1362, 1364, 1371, 1372, 1373, 1374, 1377, 1378, 1384, 1385, 1388, 1390, 1391, 1392, 1393, 1395, 1399, 1400, 1401, 1404, 1406, 1407, 1409, 1410, 1411, 1413, 1414, 1416, 1417, 1421, 1423, 1424, 1425, 1426, 1428, 1429, 1430, 1432, 1433, 1434, 1435, 1437, 1438, 1439, 1440, 1442, 1443, 1444, 1446, 1450, 1456, 1458, 1462, 1465, 1467, 1468, 1469, 1471, 1472, 1474, 1476, 1477, 1478, 1479, 1480, 1485, 1486, 1489, 1490, 1491, 1493, 1494, 1495, 1496, 1497, 1506, 1509, 1510, 1512, 1514, 1515, 1517, 1518, 1519, 1520, 1523, 1525, 1527, 1528, 1531, 1533, 1534, 1538, 1539, 1540, 1541, 1544, 1545, 1548, 1549, 1551, 1552, 1555, 1556, 1561, 1563, 1564, 1565, 1569, 1571, 1574, 1576, 1580, 1582, 1584, 1585, 1586, 1588, 1592, 1596, 1597, 1599, 1601, 1602, 1606, 1607, 1608, 1615, 1620, 1621, 1623, 1627, 1628, 1630, 1631, 1633, 1634, 1635, 1636, 1638, 1639, 1642, 1645, 1652, 1653, 1656, 1657, 1659, 1661, 1664, 1665, 1668, 1672, 1673, 1676, 1677, 1679, 1680, 1683, 1684, 1685, 1686, 1688, 1691, 1692, 1693, 1694, 1698, 1699, 1708, 1711, 1713, 1717, 1721, 1722, 1724, 1725, 1728, 1729, 1730, 1731, 1732, 1733, 1736, 1737, 1738, 1740, 1741, 1742, 1743, 1745, 1746, 1749, 1751, 1752, 1753, 1754, 1755, 1757, 1767, 1768, 1770, 1771, 1773, 1774, 1776, 1777, 1778, 1779, 1785, 1786, 1789, 1791, 1792, 2921, 2922, 2925, 2929, 2932, 2934, 2938, 2939, 2941, 2943, 2949, 2952, 2953, 2955, 2956, 2961, 2963, 2965, 2966, 2978, 2979, 2982, 2983, 2984, 2989, 2991, 2996, 2998, 2999, 3004, 3006, 3008, 3010, 3020, 3024, 3025, 3028, 3031, 3032, 3038, 3040, 3041, 3047, 3054, 3058, 3060, 3061, 3077, 3079, 3081, 3084, 3087, 3090, 3091, 3098, 3100, 3102, 3103, 3106, 3109, 3110, 3112, 3116, 3118, 3121, 3123, 3126, 3133, 3136, 3138, 3140, 3142, 3144, 3145, 3147, 3149, 3150, 3151, 3154, 3155, 3158, 3162, 3164, 3166, 3174, 3175, 3177, 3178, 3180, 3186, 3191, 3192, 3196, 3200, 3203, 3207, 3212, 3214, 3216, 3217, 3218, 3219, 3223, 3228, 3232, 3233, 3234, 3236, 3237, 3243, 3247, 3249, 3252, 3254, 3258, 3259, 3260, 3264, 3267, 3269, 3273, 3274, 3276, 3279, 3281, 3284, 3292, 3294, 3299, 3305, 3306, 3308, 3311, 3315, 3317, 3320, 3321, 3326, 3337, 3345, 3354, 3355, 3363, 3368, 3369, 3373, 3376, 3380, 3384, 3386, 3404, 3413, 3417, 3419, 3454, 3460, 3473, 3474, 3479, 3485, 3487, 3492, 3522`

## Write-phase gate

Any future write invocation must provide this exact training family, outcome-definition PK, cutoff/date range, reviewed manifest path, request key, actor, and explicit `--approve-write`. The reviewed file's manifest hash must match. A different scope produces a different deterministic request key. No write is authorized by this document.
