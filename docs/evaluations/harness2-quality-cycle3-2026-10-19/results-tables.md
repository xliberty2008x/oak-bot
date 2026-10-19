| Cycle | k | Producers / roles | Regression joint baseline → candidate | Fresh joint baseline → candidate | Fresh W/L | Regression losses | Decision |
|---|---:|---:|---|---|---:|---:|---|
| 1 | 1 | 12 / 4 | 1/2 → 1/2 | 1/2 → 1/2 | 1/1 | 1 | REJECT |
| 2 | 1 | 12 / 4 | 1/2 → 1/2 | 1/2 → 1/2 | 1/1 | 1 | REJECT |
| 3 | 2 | 28 / 8 | 3/4 → 2/4 | 5/6 → 2/6 | 1/4 | 2 | REJECT |

| Cycle | Split | Arm | Joint / tasks | Mean property score | Seconds sum | Tools | Reviews | Unverified | Failed/incomplete |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | holdout | baseline | 1/2 | 0.867990 | 210.745 | 43 | 2 | 0 | 0 |
| 1 | holdout | candidate | 1/2 | 0.840159 | 165.819 | 38 | 2 | 0 | 0 |
| 1 | regression | baseline | 1/2 | 0.868936 | 185.543 | 37 | 2 | 0 | 0 |
| 1 | regression | candidate | 1/2 | 0.863053 | 198.354 | 39 | 2 | 0 | 0 |
| 2 | holdout | baseline | 1/2 | 0.884432 | 194.946 | 38 | 2 | 0 | 0 |
| 2 | holdout | candidate | 1/2 | 0.881386 | 181.665 | 37 | 2 | 0 | 0 |
| 2 | regression | baseline | 1/2 | 0.863365 | 191.474 | 40 | 2 | 0 | 0 |
| 2 | regression | candidate | 1/2 | 0.880703 | 164.188 | 35 | 2 | 0 | 0 |
| 3 | holdout | baseline | 5/6 | 0.926475 | 509.193 | 108 | 7 | 0 | 0 |
| 3 | holdout | candidate | 2/6 | 0.842589 | 518.198 | 127 | 6 | 0 | 0 |
| 3 | regression | baseline | 3/4 | 0.917352 | 342.909 | 72 | 4 | 0 | 0 |
| 3 | regression | candidate | 2/4 | 0.844764 | 312.807 | 72 | 4 | 0 | 0 |

| Cycle | Producer seconds / tools / reviews | Role seconds / tools | Interrupted producer attempts | Unknown / unverified pair arms |
|---|---:|---:|---:|---:|
| 1 | 1114.522 / 232 / 12 | 190.043 / 125 | 0 | 0 / 0 |
| 2 | 1096.066 / 220 / 12 | 176.551 / 113 | 0 | 0 / 0 |
| 3 | 2375.038 / 523 / 29 | 333.209 / 298 | 0 | 0 / 0 |

JSON-only audit; this does not independently attest visuals or the model transport. Property scores include completed failures. Missing values and unknown executions remain explicit in JSON. Cost tables cover cycle work only; no USD cost is inferred.
