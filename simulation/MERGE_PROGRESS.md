# SDD ledger — plan: simulation/MERGE_PLAN.md

Ruling: 用户已批准INTERFACE_BOUNDARY并要求直接精简当前代码；就地实施，不移动工作树、不创建分支、不提交/推送；保存原有dirty改动和批注。只读历史引擎用于测试，不作为正式依赖。

Pre-flight: Task 2使用Task 1 GPU路径/资源接口；Task 3使用Task 2原生及新分解重载与Task 1计价。统一pending按原生整数单位、费用按浮点字节换算；两处物理端点均为(s,u)→(d,u)。

Baseline: 原生make build成功，备份核心源码到 /tmp/fast-merge-before.Ww7Sbi/core.tgz；Local与Matrix摘要已记录。无新代码前未存在主项目check入口。

Task 1: complete — 缺ResourceInfo/Topology接口的编译RED已观察；固定路径、部分共享资源、浮点字节、旧接口与GPU-only拒绝测试GREEN。
Task 2: complete — 缺ScheduleMode/decompose重载的编译RED已观察；两机制分别81014→54012.6us、35014→30012.6us，原生置换/系数一致、输入未修改、真实量清零测试GREEN。
Task 3: complete — 缺set_schedule的RED已观察；make -C simulation check通过C++接口及3个Python测试，包含26组×3策略共78份历史逐阶段输出完全对齐。覆盖非整数字节、factor=3缩放、不同GPU路径、真实量守恒与非法输入。迁移测试曾错误按两字段初始化三字段link_info_t，已改为显式alpha/beta；不是生产算法不一致。scan旧入口缺Topology链接的RED已观察并补齐。
Task 4: complete — README与接口文档更新，未增加核心类文件；旧目录保留为历史工具。Local/Matrix四文件摘要均与实施前一致。一次独立审查完成，收尾修复后topology/scan/planning/bottleneck/refine/ordering/scenario/independent/boundary九套check全部exit=0（45个Python测试及各自C++测试）。主项目check exit=0（C++接口与异常释放测试、3个Python测试含78份历史逐阶段对照）。git diff --check通过。

Validation refactor: 输入路径校验使用已缓存的路径/最小带宽，不再为每条活跃流分配完整资源计数数组；候选真实量守卫使用整数原生单位，避免以浮点字节判断守恒。78份历史结果仍完全对齐。

Final review: /root/review_native_merge 独立只读审查，仅对比实施前tar快照，未混入用户原有dirty改动；未发现匹配/计价/兼容性回归，独立重跑主测试和78份迁移对照通过。
Final: Ruling: 将异常路径缓冲泄漏列为本次必须解决项；调度批量运行时可能反复泄漏整个计划缓冲，不应只按最小2节点示例的16字节评估。用两个连续vector存储替代N²+NK次new[]，Local接口仍借用原始指针；代价是缓冲需连续分配，峰值数据规模不变。
Final: fixed 异常路径计划缓冲泄漏 — exception_cleanup_tests在缺失路径时RED已观察；两个异常阶段（分解前校验失败、dispatch后计价溢出）的原生数组存活计数均GREEN，主套件及78份迁移结果GREEN。
Final: cleanup 完成时间策略不再构造未使用的两份GpuTransfer列表；构造仅留在公共资源策略分支，属于用户要求的消冗余，不改变匹配或目标；主套件及78份迁移结果GREEN。
Final: Ruling: 全局最优性、任意负载必然提速、大规模绝对规划时延不作为本次归并的新增承诺；维持现有策略和费用模型以便严格迁移对照。代价是仍需后续性能实验验证在线规划预算，不能由功能测试推断。

Final verification: /tmp/fast-merge-final-main.log；九套历史日志 /tmp/fast-merge-final-{topology,scan,planning,bottleneck,refine,ordering,scenario,independent,boundary}.log；ASan+UBSan接口测试exit=0，日志 /tmp/fast-merge-sanitizer-final.log。ASAN_OPTIONS=detect_leaks=0，不声称原生工程完全无泄漏；新增异常释放使用原生数组分配/释放计数独立验证，Local历史内存管理按边界未修改。干净独立BUILD_DIR构建成功，日志 /tmp/fast-merge-clean-build.log。
Final state: 只在现工作树保留变更，不提交/推送；保留实施前 /tmp/fast-merge-before.Ww7Sbi/core.tgz。无递归清理、无历史目录删除。正式构建不链接历史Planner/策略源码；原有批注保留，仅随缓冲所有权更新相邻说明。
Scope size: 相对实施前tar快照的非空差异行（含注释），all2all.cpp +187、all2all.h +22；model.cpp +72、model.h +27；global.cpp +56/-22、global.h +7；main.cpp +69/-2。无新增核心算法/配置类文件；新文件仅接口说明、计划记录及测试。以上不是相对HEAD，避免将用户原有批注计为本次新增。
