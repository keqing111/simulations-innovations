# 2. Background and Motivation

## 2.1 Endpoint-Feasible Decomposition

All-to-all with variable message sizes, denoted all-to-allv, is specified by a nonnegative demand matrix D, where D[i,j] is the number of bytes that endpoint i must deliver to endpoint j. A permutation stage allows each endpoint to send to one destination and receive from one source. This restriction avoids endpoint incast but does not imply that concurrent transfers use independent network paths. FAST combines intra-server rebalancing with balanced, one-to-one scale-out transfers [1](https://www.usenix.org/conference/nsdi26/presentation/lei-yiran).<!--ref:P00--><!--anchor:quote:FAST%20addresses%20skew%20through%20intra-server%20rebalancing-->

In the complete Birkhoff–von Neumann (BvN) formulation considered here, an auxiliary matrix A with equal row and column sums is decomposed into weighted permutation matrices:

```text
A = sum over stages k of a[k] * Pi(p[k]).
```

The vector p[k] lists each source's destination, Pi(p[k]) is its binary permutation matrix, and a[k] is the stage coefficient in bytes. Each iteration finds a perfect matching in the positive residual support, extracts the smallest selected residual, and subtracts the corresponding weighted permutation. Padding may make A larger than D; this extra capacity is not additional application traffic. Only actual delivered bytes should contribute to network resource loads.

The number of stages and their coefficients describe decomposition granularity. They do not specify how transfers sharing an intermediate link are grouped. Thus, even a minimum-stage decomposition can leave room to improve communication time through matching selection.

## 2.2 Shared Resources and Stage Cost

We consider a fixed-path hierarchical network. A resource e has bandwidth B[e], and the path between endpoints i and j is a fixed resource set R[i,j] with one-way propagation time tau[i,j]. Resources may represent server ports, gateway ports, or directed inter-datacenter links. Routes, capacities, and propagation parameters are known before scheduling; the concurrent load on each resource still depends on the selected matching and actual residual demand.

Let x[k,i,j] be the true bytes sent from i to j in stage k. We use the following static capacity approximation:

```text
L[k,e] = sum of x[k,i,j] over transfers whose paths contain e
s[k]   = max over resources e of L[k,e] / B[e]
t[k]   = max over active transfers (i,j) of tau[i,j] + s[k].
```

Bandwidth and bytes use consistent units. With bandwidth in Gbps, serialization time in microseconds is `0.008 * bytes / Gbps`. Here s[k] captures aggregated resource work, while the propagation term is charged once per active stage. It is not an extra round-trip-time penalty.

This approximation does not simulate packet queues, acknowledgments, or early completion of individual flows. In general, the two maxima may arise from different transfers. The motivating example below isolates resource grouping under this declared model: its stages have identical propagation, and its intra-server costs are zero. The reference FAST pipeline therefore reduces to the sum of network-stage times without adding an artificial barrier.

## 2.3 A Minimum-Stage Counterexample

Consider three data centers (DCs), each containing two servers, a top-of-rack switch, and a gateway. Servers S0 and S1 belong to DC0, S2 and S3 to DC1, and S4 and S5 to DC2. Each server and gateway port provides 400 Gbps. Every directed DC pair has a 100-Gbps wide area network (WAN) link; opposite directions are independent. Total one-way propagation is 1.4 microseconds within a DC and 52.8 microseconds across DCs.

For clarity, each server initially has one communication endpoint. Let Q = 1 MB = 1,000,000 bytes. The demand matrix is:

```text
D / Q = [ 0 1 0 0 1 0
          1 0 0 1 0 0
          0 1 0 0 1 0
          1 0 0 0 0 1
          0 0 1 0 0 1
          0 0 1 1 0 0 ].
```

Every row and column sums to 2Q, so no padding is required. Each row has two positive entries; hence at least two permutation stages are necessary. Because every positive entry equals Q and each iteration extracts the actual minimum, every complete BvN decomposition of this matrix has exactly two stages with coefficients (Q,Q).

Our reference FAST implementation produces the following permutations with native scaling either enabled or disabled:

```text
p[0] = [1,0,4,5,2,3]
p[1] = [4,3,1,0,5,2].
```

These are observed outputs, not an unfavorable schedule manually supplied to the baseline. In the first stage, S2→S4 and S3→S5 share WAN1→2, while S4→S2 and S5→S3 share WAN2→1. Each link carries 2Q. In the second stage, S2→S1 and S3→S0 place 2Q on WAN1→0. Both stages therefore incur 160 microseconds of serialization.

A different legal decomposition is:

```text
q[0] = [4,0,1,5,2,3]
q[1] = [1,3,4,0,5,2].
```

Both permutations satisfy the endpoint constraints, and each has actual residual minimum Q when executed in order. Their weighted sum reconstructs the same D. They change neither the number of stages nor the amount delivered on any endpoint pair or network resource.

Table 1 shows the resulting directed WAN loads in units of Q. Unlisted links carry no data.

| Directed WAN | p[0] | p[1] | q[0] | q[1] |
|---|---:|---:|---:|---:|
| DC0→DC1 | 0 | 1 | 0 | 1 |
| DC0→DC2 | 0 | 1 | 1 | 0 |
| DC1→DC0 | 0 | 2 | 1 | 1 |
| DC1→DC2 | 2 | 0 | 1 | 1 |
| DC2→DC1 | 2 | 1 | 2 | 1 |
| Maximum WAN load | 2 | 2 | 2 | 1 |

The alternative overlaps work on WAN1→0 and WAN1→2 with the work on WAN2→1 instead of allowing them to create separate two-unit bottlenecks. Its stage serialization times become 160 and 80 microseconds. Server and gateway resources are faster and do not determine these stage costs. Both stages in both schedules contain inter-DC traffic, so each pays the same 52.8-microsecond propagation term:

```text
T(reference)   = 2 * 52.8 + 160 + 160 = 425.6 microseconds
T(alternative) = 2 * 52.8 + 160 +  80 = 345.6 microseconds.
```

The alternative reduces modeled communication time by 18.8%, without reducing stages or propagation charges. This is a constructed mechanism example, not an average performance claim. The reference permutations may change with endpoint numbering or matching traversal order; the example establishes the absence of a topology-optimality guarantee from endpoint feasibility, not that every FAST build must produce this schedule.

## 2.4 Capacity Bound and Design Implication

The improvement is supported by a matching lower bound. WAN2→1 must deliver 3Q, requiring 240 microseconds at 100 Gbps. The sum of per-stage maximum resource work cannot be smaller than the total work of this individual resource. Moreover, S2 has only inter-DC destinations, so each of the two complete permutation stages contains WAN traffic. Their total propagation charge is therefore 105.6 microseconds. The alternative attains the resulting bound of 345.6 microseconds within this model and decomposition class.

The reference result was reproduced through the original FAST pipeline with zero balance, intrinsic, and dispatch costs. For a graphics processing unit (GPU) extension, each server contains four GPUs. Each source GPU u has the same demand to GPU u on the destination server. This preserves the server-level decomposition and requires no intra-server movement. Reference pipeline time becomes 1385.6 microseconds, while independent resource replay of the alternative gives 1065.6 microseconds, again reaching the corresponding model bound. Enabling native scaling does not change either reference schedule. The alternative remains a feasible witness, not the output of an implemented new optimizer.

The example separates two scheduling decisions: decomposition granularity determines stage coefficients and count, whereas resource grouping determines which link workloads overlap within those stages. Improving the former does not automatically optimize the latter. A topology-aware scheduler should therefore use true concurrent resource loads to guide matching selection while controlling fragmentation and planning cost. The following method must address this joint decision; endpoint feasibility, larger coefficients, or a rule that merely keeps WAN traffic present in every stage is insufficient.
