# Hermes memory provider benchmark

Scenario: 4 sessions, 20 turns, 6 cold-start probes. Identical scripted transcript per provider; full provider shutdown between sessions (cold-start recall only).

| Provider | Packet recall | Stale leaks | Median packet tokens | Standing overhead tok/turn | Effective tok/turn | Median recall ms | Net calls (ingest/recall) | Offline recall | Model-visible tools |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| cortext | 10/14 facts | 0 | 125 | 0 | 125 | 63 | 0/0 | 6/6 probes | 0 |
| holographic | 0/14 facts | 0 | 0 | 632 | 632 | 0 | 0/0 | 0/6 probes | 2 |
| holographic-tools | 0/14 facts | 0 | 0 | 632 | 632 | 0 | 0/0 | 0/6 probes | 2 |
| mem0 | 8/14 facts | 1 | 131 | 304 | 435 | 459 | 9/1 | 0/6 probes | 3 |
| tencentdb | 4/14 facts | 1 | 970 | 417 | 1387 | 169 | 0/0 | n/a (sidecar) | 2 |

## Blind judge (LLM answers from each packet)

| Probe | control | cortext | holographic | holographic-tools | mem0 | tencentdb | Winner |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| vet-supersession | 1 | 9 | 1 | 1 | 2 | 10 | tencentdb |
| deploy-process | 1 | 8 | 1 | 1 | 10 | 8 | mem0 |
| auth-owner | 0 | 10 | 0 | 0 | 0 | 0 | cortext |
| travel-plans | 2 | 10 | 2 | 2 | 2 | 6 | cortext |
| language-preference | 2 | 10 | 2 | 2 | 10 | 2 | tie |
| unknown-bait | 10 | 10 | 10 | 10 | 10 | 10 | tie |

Wins: cortext: 2, tie: 2, tencentdb: 1, mem0: 1
