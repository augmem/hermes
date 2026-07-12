# Hermes memory provider benchmark

Scenario: 4 sessions, 20 turns, 6 cold-start probes. Identical scripted transcript per provider; full provider shutdown between sessions (cold-start recall only).

| Provider | Packet recall | Stale leaks | Median packet tokens | Standing overhead tok/turn | Effective tok/turn | Median recall ms | Net calls (ingest/recall) | Offline recall | Model-visible tools |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| cortext | 8/14 facts | 1 | 97 | 0 | 97 | 16 | 0/0 | 6/6 probes | 0 |
| holographic | 0/14 facts | 0 | 0 | 646 | 646 | 0 | 0/0 | 0/6 probes | 2 |
| holographic-tools | 0/14 facts | 0 | 0 | 646 | 646 | 0 | 0/0 | 0/6 probes | 2 |
| mem0 | 8/14 facts | 1 | 122 | 302 | 424 | 505 | 9/1 | 0/6 probes | 3 |
