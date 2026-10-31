# Post-result ideas — not validation results

These are exploratory derivations after run-33e58e040218, not implemented policies, not measurements, not grounds for tuning already-used validation seeds.

1. Joint granularity/network choice: start from a Max-Min-feasible threshold support graph, try bounded topology cycles within that graph. At the current residual the min coefficient cannot fall below the maximum feasible threshold, unlike arbitrary force-edge repair. This still gives no guarantee on future residuals/stage count; compare known Max-Min fairly and use fresh validation.

2. DC-structured construction on a strictly balanced6server/3DC/2server-per-DC example. All offdiagonal server entries equalq. The following5permutations cover every directed offdiagonal server pair once, so they are a valid Birkhoff decomposition with coefficientq, not permutation enumeration:

```
local: [1,0,3,2,5,4]
WAN0:  [2,5,4,1,0,3]
WAN1:  [4,3,0,5,2,1]
WAN2:  [3,4,5,0,1,2]
WAN3:  [5,2,1,4,3,0]
```

Each WAN stage has exactly1server flow per directedDCpair; the local stage contains no WAN. Thus5totalstages but4WANstages, unlike current native/cached5WANstages. Pure local-first may beat a WAN-coverage rule in this model. Matrix cover/perfect-match property checked independently in Python, but full native dispatch/pipeline evaluation of this constructed schedule has NOT been performed. Need native execution, canonical input checks, and precise bandwidth/delay limits before reporting speedup. Arbitrary Zipf traffic does not admit this construction directly, so it is a diagnostic/witness route, not a general algorithm.
