# Automated bisect result

The deterministic PostgreSQL reproducer was used as the bisect predicate. The endpoints were
`6e1f842` (good) and `e5217fd` (bad).

| Revision | Result |
|---|---|
| `6d4032f` | good |
| `3f192eb` | good |
| `e12df85` | good |
| `6250fa4` | bad |

First behavior-changing commit: `6250fa43b470e58f01275d7281e1539383f0639a`, **Fence durable
writes only at outer commit**.

Before that commit, releasing the nested enqueue savepoint registered the pipeline Session as the
fenced Session. The next heartbeat therefore reused the pipeline Session. After that commit, the
nested savepoint correctly stopped being treated as a durable publication boundary, but the
ambient ownership context no longer had a bound Session. The heartbeat opened its normal detached
Session, inherited the ambient domain ownership, and its `commit()` hook tried to acquire
`FOR UPDATE` on the root job. Meanwhile, the pipeline Session's uncommitted child insert held the
foreign-key transaction lock rooted at that job. The process waited on itself.

This is a transaction-authority regression, not an SEC network or parsing failure. The durable fix
binds domain authority to one explicit Session and makes control transactions explicitly detached.

