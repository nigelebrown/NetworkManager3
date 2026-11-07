# Experience and health scoring

Everything here is computed by `backend/experience/scoring.py` (pure functions) from data SIMS actually collected.
**No input is ever estimated.** An input is either *measured* (with a stated source) or *unavailable*.

## Client experience score (0-100)

Each measurable input is converted to a 0-100 sub-score on a straight line between a `worst` value (scores 0)
and a `best` value (scores 100), clamped at both ends. The score is the weighted average of the sub-scores
**that could be measured**; missing inputs are dropped and the remaining weights renormalized.

| Input | Default weight | worst → best | Source |
|---|---|---|---|
| signal | 0.45 | SNR 10 dB → 35 dB (RSSI -85 → -60 dBm is used only if SNR is missing) | AP-reported |
| rate | 0.20 | 12 Mbps → 150 Mbps (the AP's "Speed" column; direction unspecified) | AP-reported |
| stability | 0.25 | 6 disconnects in 24 h → 0 | **inferred from polling** |
| retries | 0.10 | 30 % → 5 % retransmissions | AP-reported, **not collected yet** (always unavailable) |

`score = Σ(subscore × weight) / Σ(weight of measured inputs)`, rounded to a whole number.

Rules that keep it honest:

- **coverage** = share of the model's total weight that was measurable. Every result reports it, plus which inputs were
  missing and why. If coverage is below `min_coverage` (default 0.30) there is **no score** (shown as unavailable), never 0.
- **Stability** needs the client to have been observed for at least `min_observation_minutes` (default 30). A brand-new
  client does not get a free 100.
- Disconnects are counted from `client_events`, which SIMS **infers by comparing successive polls**. A client that
  connects and leaves between two polls, or roams twice, is invisible. The poll interval (default 60 s) bounds this.
- If `show clients` fails on a poll, SIMS keeps the previous client state and records **no** disconnects for that poll.

### Bands

| Band | Score |
|---|---|
| good | ≥ 75 |
| fair | ≥ 50 and < 75 |
| poor | < 50 |
| unavailable | no score |

## Access point score

| Input | Default weight | worst → best | Source |
|---|---|---|---|
| clients | 0.50 | mean score of the AP's connected clients | derived |
| utilization | 0.25 | 80 % busy → 40 % (worst radio) | AP-reported, **not collected yet** |
| load | 0.15 | 60 clients → 20 clients | AP-reported |
| resources | 0.10 | CPU or memory 95 % → 60 % (the worse of the two) | AP-reported, **not available on `show aps`** |

An AP that is not up has no score (band `offline`).

## Site / network score

The mean of the scores of connected clients that could be scored, with the good / fair / poor split and the number of
clients that could not be scored. It is stored on every poll for the history chart.

## What is NOT measured (and therefore not scored)

Retransmission rate, channel congestion, association/authentication/DHCP failures, roaming performance, latency, jitter,
packet loss and application responsiveness. They need collectors that do not exist yet (RF commands) or active probes /
client agents. They are listed as unavailable, not estimated.

## Alerts

Defaults, all adjustable in **Experience → Scoring settings** (`alerts` section):

| Alert | Fires when | Resolves when |
|---|---|---|
| poor_experience (per client) | band is *poor* for 5 consecutive polls | the band is no longer poor |
| excessive_disconnects (per client) | 5+ inferred disconnects in 60 min | fewer than 5 |
| high_utilization (per AP) | utilization ≥ 80 % in each of the last 5 samples | the latest sample is below 80 % |
| ap_reboot | AP uptime goes backwards by more than 60 s | manual |
| firmware_mismatch (per site) | APs in one site report different versions | versions agree |
| ap_offline, poll_failed | AP missing/down, site unreachable | automatically on recovery |

`high_utilization` and `ap_reboot` need utilization and uptime data, which `show aps` does not provide, so they stay
silent until those collectors exist. `firmware_mismatch` needs per-AP firmware (today every AP is given the cluster's
version), so it stays silent too. Alert text uses masked client MACs.

## Network health score (dashboard tile)

`health = 70 × (APs up / APs total) + 30 × (sites polled OK / sites)`, 0-100. It reflects availability only.
