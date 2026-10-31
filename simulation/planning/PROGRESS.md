# SDD ledger — plan: simulation/planning/PLAN.md

2026-10-30。用户批准冻结A1、做语义等价的规划实现优化并补公平拓扑对照。

Ruling: 原地授权dirty checkout、源码归档和新planning/结果，不做Git提交/清理——保留用户和历史数据；若需集成留待后续。
Pre-flight Task1→Task2: optimized实现通过标签选择，策略本身不变；SPM权重来自真实残余和路径缓存，必须套同q*/接受guard。
Pre-flight Task2→Task3: 12标签同进程24轮转；去实现标签后所有语义字段完全一致，不能只比较CCT。
Ruling: 外部论文约束不同，SPM只作为文献启发的同模型控制，不冒称完整外部SOTA复现——避免模型不公平，但投稿仍需明确外部对照边界。

- Baseline ordering5tests passed; current A1/native sources byte-identical to archived ordering reference.
- Profiling complete: planning/profile-before artifacts, 21 A1 executions under-pg; threshold DFS34.23%, resource-construction DFS23.15%, native DFS8.72% samples. Profiled times are NOT fair benchmark times.
- Task1 RED: optimized API absent, then stub42→42allocations assertion failed; GREEN matcher42→1allocations, stable ties/displacement/failure reuse and30random comparisons.
- Task2 RED: weighted API absent; GREEN80tiny brute-force assignment objectives plus real-vs-dummy/path utility. Full CLI/native regression pending Task3.
- Task3 RED: harness absent; GREEN3Python tests, including real compiled24-outcome smoke replay, all-field pair guard and source/metadata tamper rejection. C++ kernels also passed. All old suites running before formal freeze.
- Regression: ordering5/scenario3/refine2/scan5/bottleneck2/topology8/reproduction7/timing-audit2 Python tests all green (34existing); corresponding C++ checks green. New planning3Python+C++ green.
- Formal source/grid freeze: explore8 and confirm32 configs,12labels,24rotations. Both prepared before formal outcomes; no source/test edits permitted without new archive. No benchmark-vs-benchmark concurrent CPU jobs; old regressions/builds exited first.
Ruling: 小规模SpreadOut负控制有显著计时差异，固定邻接顺序可能保留分配器前序效应；追加独立进程单策略暖机、两块反序诊断（N48/seed760201的8配置、每块12次）——不改冻结网格/算法/归档，以同二进制全语义相同为前提。诊断为事后验证，不能冒充新独立确认集；额外CPU成本换取更可靠解释。
- Formal completed: explore96+confirm384 outcomes;11520 measured samples. Both full replay audits passed, ref/opt schedules/counters/payload/cost exactly equal for all240 pairs. Isolated diagnostic running only after formal job exited.
- V1 isolated96outcomes/2304samples completed. F000/SpreadOut controls GM≈1; threshold-enabled maxmin/X10/SPM regress≈7–8% on Zipf, while A1 improves≈5.7% Zipf/11.0%balanced. Historical116 and archive-only rebuild36 checks passed for V1.
Ruling: 直接递归阈值核在真实Zipf负控制上退化，撤回此快路径，只保留资源工作区优化。保留V1全部归档；最终版另建源码/二进制，对全部40配置480结果及116历史输入核验语义相等，仅重新采集预定8配置的隔离CPU诊断——不重复已等价证明的整批通信测量，不跨版本拼接CPU；若等价失败则旧通信数据不得用于最终版。
- Final kernel RED: Max-Min ref/opt allocation control35/34 failed; GREEN after restoring common threshold kernel35/35; resource kernel42→1 remains. planning3Python+C++ green; validation3Python GREEN (new bridge field-tamper guard and registered-build override check).
- Final old regression rerun:34existing Python tests+C++ all green, total40Python including new6. No production changes after final-96b18da3da36 snapshot.
- Final build final-96b18da3da36:480full semantic equalities+116archived-binary historical equalities passed, independently reconstructed including all old q*diagnostics. Final-pg diagnostic complete; frozen-source plots rendered with derived comparison.json byte-identical.
- Final isolated8configs/2304samples started after bridge/profile/regression/render jobs all exited. CPU sources and two versions kept separate.
- Final isolated completed:96outcomes/2304samples, no-op aggregate GMs0.994–1.014; A1 GM1.11527, individual1.07944–1.13509. Zipf ref152.9727→opt136.5089ms medians. Final CPU figures/provenance generated and visually checked;41Python+C++green. Current source identity still equals final build;13protected native source hashes unchanged.
- Task1 complete:41Python/C++checks green, pooled matcher42→1 allocations, threshold35/35 common kernel.
- Task2 complete:SPM exact masked objective, virtual exclusion, route/payload/native pipeline audits and12labels passed.
- Task3 implementation/evidence complete:480CCT bridge+116historical, final CPU separate, reports/figures/outline; one fresh-context read-only review pending.
- Final fresh-context review completed by gpt-6-astra/high:0Critical,0Important,2Minor. Regraded by effect: keep both Minor; main planner never expects a fully infeasible q*graph, reset logic appears correct; current comparison data verified, future table-origin automation gap remains. No source fix pass required. Coverage wording corrected to avoid claiming full-false recovery was tested.
Final: minor (deferred): append feasible real-payload cache call after a completefalse matcher return; current tests exercise failed branches and successful-call reuse, not that recovery path.
Final: minor (deferred): bind plotting comparison tables to registered audited raw derivation, not only registration/bridge binary metadata. Current plotted values verified; future stale/replaced tables still need manual audit.
Final: Ruling: unrelated pre-existing native/comment edits remain outside review—protect13source hashes, preserve dirty work—existing defects are not ruled out.
Final: Ruling: physical/packet/real-deployment accuracy is not established—keep analytical-model claim only—no real-network/congestion guarantee.
Final: Ruling: external solver reproduction/superiority is not claimed—SPM remains self-built control—external strong comparison incomplete.
Final: Ruling: novelty/EI acceptance/universal superiority not judged as established—report scoped gains and counterexamples—paper positioning/evidence work remains.
Final: Ruling: historical dependency packaging remains explicit—116checks require named ordering archives—portable transfer must include them.
Final: Ruling: cross-machine CPU/deadline/allocator-only attribution unsupported—final CPU limited to8config local posthoc diagnostic—do not extrapolate or treat it as independent algorithm confirmation.
- Additional final isolated audit:192blocks all command/hash/sample-count/semantic checks and independent reconstruction pass. Frozen counterexample full pipeline root verified2729.429957µs; source links/figures checked.
- Task3 complete:41Python+C++green;480bridge+116history+192isolated-block audit; final review clean of blocking findings;2minors explicitly deferred. Final source identity stable, user changes preserved, no Git mutation, workspace/artifacts retained per authorization.
