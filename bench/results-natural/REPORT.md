# Hermes memory provider benchmark

Scenario: 4 sessions, 20 turns, 6 cold-start probes. Identical scripted transcript per provider; full provider shutdown between sessions (cold-start recall only).

| Provider | Packet recall | Stale leaks | Median packet tokens | Standing overhead tok/turn | Effective tok/turn | Median recall ms | Net calls (ingest/recall) | Offline recall | Model-visible tools |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| cortext-natural | 4/14 facts | 0 | 208 | 0 | 208 | 8 | 0/0 | 6/6 probes | 0 |
