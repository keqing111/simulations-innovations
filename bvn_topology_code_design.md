# 异构拓扑 A2A：BvN 调度工具的代码与接口设计

日期：2026-11-09。状态：首版实现、数值修复与代码审查已通过；算法性能收益尚未验证。实施记录见 本地实施报告（审查工作区留存），后续场景见 [测试矩阵](bvn_topology_test_matrix.md)。最终新测试5323条断言通过，旧C++及3项Python回归（含78份历史输出对齐）通过。

本设计对应 [研究方案清单](fast_bvn_topology_research_plan.md)，并采用后续讨论确定的新边界：以无补齐的部分匹配为主线，保留 FAST 原生分解作为对照，不经过 balance–dispatch，不接入 SimAI/ns-3。旧方案清单中有关完整置换、补齐和 pipeline 的约束，以本文为准。

## 1. 最小代码布局

只新增三个生产代码文件、一个测试文件；不为每个 S/G/L/F 方案创建类、目录或注册框架。

以下路径相对于 `FAST/simulation/`：

| 文件 | 动作 | 内容 |
|---|---|---|
| `algorithm/bvn_schedule.h` | 新增 | 公共数据结构、策略配置、规划/验证/评价函数声明 |
| `algorithm/bvn_schedule.cpp` | 新增 | FAST 适配、部分匹配循环、候选生成/评分/选择、调度验证与评价编排 |
| `bvn_main.cpp` | 新增 | 文件读取、策略参数、计时、结果输出；独立可执行入口 |
| `tests/test_bvn_schedule.cpp` | 新增 | 对齐、守恒、计时和组合兼容性测试 |
| `Makefile` | 小幅扩展 | 增加 `bvn-build`、`bvn-check` 目标，保留原构建目标 |
| `algorithm/all2all.h/.cpp` | 复用，不改算法 | 直接调用原生 `FastAll2All` 补齐和分解路径 |
| `algorithm/matrix.h/.cpp` | 复用，不扩展类型 | 仅 FAST 适配使用原整数矩阵 |
| `topology/model.h/.cpp` | 复用现有接口 | 静态固定路径、带宽、传播时延和阶段计价 |
| `scheduler/config.h/.cpp` | 链接复用 | 满足原生 `FastAll2All` 的链路参数符号依赖 |
| `scheduler/local.*`、`scheduler/global.*` | 不接入新入口 | 保留旧程序与历史实验 |
| `independent/`、`scan/` 等历史目录 | 不作为新目标的构建依赖 | 可参考其中方法，不整体复制旧 Planner 类 |

没有新增 `Planner`、`CandidateGenerator`、`CandidateSelector`、`Simulator`、第二个 `Topology` 等类。上述职责用同一命名空间内的函数和少量结构体实现。原生 FAST 的完整置换数据结构与新部分匹配语义不同，故不将新逻辑继续塞进 `FastAll2All` 或 `PermutationSet`。

`bvn_schedule.cpp` 按“适配、分解、候选、验证/评价”分段；后续只有确实出现独立维护需要时才拆文件。JSON、批量扫描、绘图不进入算法文件。

## 2. 现有组件如何接线

```text
bvn_main.cpp
  ├─ 读取 Demand、资源表、固定路径表、Options
  ├─ Topology::from_fixed_routes(N, 1, resources, paths)
  ├─ bvn::plan(...)
  │    ├─ FastNative → Matrix → FastAll2All → p_sets → 真实字节 Stage
  │    └─ Partial    → 真实残余 → 候选匹配 → 评分/选择 → Stage
  ├─ bvn::validate_schedule(...)
  └─ bvn::evaluate_schedule(...)
       └─ Topology::evaluate_gpu(stage.flows)
```

### 2.1 输入端点的含义

- `N` 表示调度端点数量。当前可以解释为服务器，但需求是端点对的总字节，不是 FAST 的每 GPU 代表容量。
- 直接输入 N×N 非负整数需求；对角线必须为零。机内业务不在本工具中计时，也不能静默吞掉非零对角线。
- 源和目的属于二分图两侧：一个端点同一阶段可以发送一次、接收一次，发送和接收对象可不同。
- 初始版本沿用 `Topology` 的 N≤64 限制，输入超限时报错；以后扩容需单独调整和测试，不能宣称已支持任意规模。

### 2.2 复用 Topology 的方式

使用 `from_fixed_routes(N, 1, ...)`：将一个研究端点映射为现有 API 中一个逻辑 server、一个逻辑 GPU。此处 `g=1` 是接口映射，不是在模拟一台只有一张卡的实际机器。

因此 `GpuTransfer.src_gpu/dst_gpu` 在新入口内恰好就是端点编号；`evaluate_gpu()` 的同 server 过滤只会过滤自环，不会误删同 DC 的跨端点流量。新入口只使用整数资源 ID 路径接口，不调用旧服务器视图的 `evaluate(vector<Transfer>)`，后者不支持 `from_fixed_routes` 创建的实例。

复用接口：`gpu_path()`、`resource()`、`resource_count()`、`path_min_bandwidth_gbps()`、`evaluate_gpu()`。每条有向资源只定义一次，重复使用相同 ID 表示共享容量。网卡出口、网关、WAN 均可作为资源；单向延迟显式输入，不能把 RTT 直接当单向延迟。

拓扑构建完成后视为只读。路径不选择、多路径不建模、运行中不更新带宽。外部路径表是输入真值；现有类没有物理节点连通性信息，因此它能检查 ID/容量合法性，不能凭空验证路径是否在某个未提供的物理图中连通。

## 3. 公共头文件的接口草案

以下是拟提供的接口，不要求逐字作为当前可编译代码。枚举值是明确的候选能力；未实现值必须报“不支持”，不能静默退回其他算法。

```cpp
namespace bvn {
using Topology = native_topology::Topology;
using Flow = native_topology::GpuTransfer;
using StageCost = native_topology::GpuStageCost;

struct Demand {
    unsigned n;
    std::vector<uint64_t> bytes;       // [src*n+dst]，只读原始需求
};

enum class Engine { FastNative, Partial };
enum class Space { Bytes, SerializationTime, DelayAdjustedTime };
enum class GlobalRule { None, MaxResidual, SmoothResidual, Pressure };
enum class LocalRule { None, StageTime, SquaredWork, MaxPathTime };

struct Options {
    Engine engine;
    Space space;
    GlobalRule global;
    LocalRule local;
    bool pressure_candidate;
    bool local_edits;
    unsigned candidate_budget;        // 每轮完整候选展开/评分次数上限，含种子
    unsigned edit_attempt_budget;     // 每轮尝试修改的次数上限，含失败修改
    double min_seed_service_ratio;    // 候选真实字节/种子真实字节的最低比值
    double smooth_beta;               // 无量纲，仅平滑目标使用
    double delay_alpha;               // 无量纲，仅加性时延映射使用
    uint64_t native_unit_bytes;       // 默认1；只允许精确整除的单位换算
};

struct Stage {
    std::vector<Flow> flows;           // 仅真实、正量、非自环传输
    long double coefficient;          // 诊断量，单位由Plan.space决定
};

struct Plan {
    Space space;
    std::vector<Stage> stages;
    uint64_t decomposition_steps;     // 包括原生中纯虚拟的分解步骤
    uint64_t candidate_evaluations;
    uint64_t edit_attempts;
};

struct Validation {
    bool ok;
    long double max_pair_error_bytes;
    std::string message;
};

struct Evaluation {
    double communication_us;
    double serialization_sum_us;
    double propagation_sum_us;
    std::vector<StageCost> stages;
    std::vector<double> resource_total_bytes;
};

void validate_options(const Demand&, const Topology&, const Options&);
Plan plan(const Demand&, const Topology&, const Options&);
Validation validate_schedule(const Demand&, const Plan&);
Evaluation evaluate_schedule(const Topology&, const Plan&);
}
```

`plan()` 不修改输入，不写文件，不跑重复计时。每次调用创建一个可复用工作区并生成一份完整计划。外层入口负责墙钟计时，避免将机器时间混入算法预测时间。

`validate_schedule()` 从原始需求独立重放输出，不信任规划器的“剩余为零”断言。`evaluate_schedule()` 不使用规划器保存的候选分数，逐阶段重新调用拓扑评价器。

接口不暴露“任意评分回调+任意单位”的组合；使用有限枚举和明确兼容规则。第一批没有实现的枚举会明确拒绝，不能输出伪装成该方案的结果。

## 4. FastNative 适配：直接调用原算法

在 `bvn_schedule.cpp` 内部实现 `plan_native()`，不单独建适配器类。

```text
校验 D 的每个字节数能被 native_unit_bytes 精确整除
转换为 Matrix 所需的 uint 单位；检查元素、行和、列和的原生整数范围
构造 FastAll2All(Matrix, gpu_n=1, ..., MBpu=unit_bytes/1e6)
不调用 to_scaled_matrix()
调用 to_scaled_doubly_stochastic_matrix()
调用无参数 decompose()，不调用已有拓扑 ScheduleMode 重载

R = D
依次读取 p_sets：
    对每个 i→j，actual = min(R[i,j], get_freq()*unit_bytes)
    输出 actual>0 且 i!=j 的传输，扣减 R
    纯虚拟步骤只记 decomposition_steps，不输出执行阶段
核对 R 全部归零
```

- 第一版基线明示 `scaling=off`，不使用依赖机内参数的缩放判据。日后若加入原生缩放，作为独立对照选项，不改变默认结果。
- `native_unit_bytes` 必须显式记录，不能为绕过溢出自动向上取整需求。所有方法输入相同真实 D；单位转换只改变原生存储，不改变字节量。
- 按现有实现采取保守边界：转换后的单元≤INT32_MAX、行和/列和≤UINT32_MAX，乘回字节时也检查溢出。超过边界时明确拒绝该 FAST 对照，不截断。
- `gpu_n=1`、链路枚举仅满足原构造接口；不进行本地调度。原生 `PermutationSet` 仍保留完整匹配语义，不往其中写 `-1`。
- 基线的真实发送量在同一阶段可以不同，因为某条边包含虚拟容量。统一 `Stage` 支持这种情况，不强制每条边的实际字节都等于公共系数。
- 适配后的 `Plan.space=Bytes`，`Stage.coefficient=get_freq()*native_unit_bytes`，以字节记录；原生内部计数单位不能直接写成字节系数。
- 直接调用当前工作区的原生无参数分支。实验记录源码版本与编译环境；不能将已含研究重载的整个文件宣称为未经修改的上游快照。

## 5. Partial 的内部组织：一个循环、多种轻量规则

以下结构与函数仅放在 `.cpp` 内部，不成为外部公共类。

```cpp
struct Workspace {
    std::vector<long double> remaining;  // 真实字节残余
    std::vector<long double> weight;     // 正需求边的固定映射权重
    std::vector<long double> residual;   // Z=weight*remaining
    std::vector<int> match, inverse;     // -1表示未匹配，不使用N偏移编号
    std::vector<unsigned char> visited;
    std::vector<double> remaining_work_us; // H_l
    // 邻接表、候选缓冲、资源累加缓冲；一次规划内复用
};

struct Candidate {
    std::vector<int> dst;               // 长度N，允许-1
    long double coefficient;
    Stage stage;
    StageCost cost;
    double global_rate;
    double local_cost;
    long double real_bytes;
};

void prepare_residual(...);
void generate_seed(...);                // 确定顺序的无权最大基数匹配
void generate_pressure_candidate(...);  // 压力排序引导的增广匹配，不声称最大权最优
bool propose_edit(...);                 // 一次有预算的增删/交换
bool materialize_and_score(...);        // 合法性、系数、实际字节、资源代价
bool better_candidate(...);             // 配置确定的约束与字典序比较
void consume_candidate(...);            // 唯一真实残余更新入口
```

### 5.1 主循环

```text
准备真实残余、固定权重、正残余支持图与剩余链路工作量
while 还有真实需求：
    生成无权部分匹配种子（非空需求必能得到非空种子）
    展开种子，作为可行回退；计入候选预算
    按配置生成压力候选、对种子或压力候选尝试有限修改
    每个不同候选重新求最小系数，恢复真实字节，计算完整分数
    在满足推进约束的候选中选择；种子始终保留
    仅选定后更新真实残余/辅助残余/H，输出一个Stage
```

支持图只含正真实需求边，默认不补齐；不使用 `Matrix::valid_sdsm()`，不调用原生要求完整匹配的 `update_permutation_sets()`。

无权基线追求最大匹配边数，但它只是一种种子。拓扑策略可以主动减少匹配规模。局部修改必须包含合法“删除一条边”，否则只用二边交换无法研究主动留空端点的收益；也可插入空闲源/目的之间的边、做合法二边交换。首版不引入无限长度的交换搜索。

无权增广实现采用固定遍历顺序。原生 DFS 依赖私有 SDSM 状态且使用不同顶点编号，不直接调用它处理未匹配行；新文件提供一个小型部分匹配函数，避免为代码复用而改写原生基线。它与原生匹配规则的差别必须写入对照解释：无补齐基线是独立策略，不将其收益全部归因为单独删除补齐操作。

候选变化后，最小系数可能改变所有选中边的字节量。因此首次实现必须重新展开整个候选，不能只更新交换的两条边后沿用旧代价。禁止要求新系数等于原生系数。

### 5.2 分解变量与真实字节恢复

| Space | 固定权重 w，单位 | 辅助残余 Z | 匹配边实际发送量 |
|---|---|---|---|
| Bytes | 1 | R，字节 | q |
| SerializationTime | `0.008 / b_Gbps`，微秒/字节 | w×R，微秒 | q/w |
| DelayAdjustedTime | `0.008/b + alpha*tau/D`，微秒/字节 | w×R，微秒 | q/w |

`q=min Z`，只在选中正需求边上计算；`tau` 为明确输入的单向传播项。加性方案在初始时满足 `Z=D/b+alpha*tau` 的单位换算版本；w 在一次分解内固定，不能随 R 重新加入一次 tau。它是调度映射，不是每阶段真实物理完成时间。

零需求边没有权重除法，也不生成候选。`consume_candidate()` 同步更新 R 与 Z；每轮至少一个真实需求项被耗尽。在精确算术下，新方案步数≤初始正需求项数。无法保证无约束的贪心部分匹配仍维持原生的最小公共行列和；也不声称阶段数小于 FAST。

### 5.3 数值边界

- 输入和原生适配是整数真实字节。Bytes 模式维持整数系数与发送量；验证要求每对精确守恒。
- 时间变换允许流体模型中的分数字节，内部采用 `long double`，传给现有拓扑接口时转换为 `double`；结果明确记录 `payload_model=continuous`。不逐阶段向上取整字节。
- 实现前固定数值容差，例如相对误差 `64*epsilon(double)` 与每对绝对误差 `1e-6 byte` 的较大值，并在结果元数据中保存。总误差不能替代逐对检查。
- 限制输入总字节≤2^53，确保整数路径在现有 double 字节计价接口内可精确表示；原生适配还有更严格的上述范围检查。
- 选定最小系数的限制边按剩余量精确耗尽，其余边按 q/w 扣减并记录舍入误差。不能把所有小残余无记录地清零；超出容差、非正推进、阶段数超过正需求项数时明确失败。
- 单位权重测试必须恢复 Bytes 的语义；允许容差的时间映射不能用“看起来接近”替代守恒检查。

## 6. 方案如何组合、量纲如何对齐

所有候选先恢复真实字节，再根据相同路径计算：

```text
H_l = 0.008 * 剩余真实字节经过资源l的总量 / B_l_Gbps
g_l = 0.008 * 候选真实字节经过资源l的总量 / B_l_Gbps
H'_l = H_l - g_l
T = 候选阶段时间预测，微秒
```

基础候选预测可复用阶段评价公式，但评分从不直接使用不同 Space 的 q 进行跨方案比较。

| 配置 | 在同一函数中的实现 | 单位/比较规则 |
|---|---|---|
| Global=None | 无全局分数 | 使用局部目标；均无目标时直接取种子 |
| MaxResidual | `(max H-max H')/T` | 无量纲，越大越好 |
| SmoothResidual | `(Phi(H)-Phi(H'))/T` | 无量纲，越大越好 |
| Pressure | `sum(pi_l*g_l)/T` | pi无量纲，结果无量纲，越大越好 |
| Local=StageTime | T | 微秒，越小越好 |
| Local=SquaredWork | `sum(g_l²)` | 微秒²，仅作同一局部规则内的比较 |
| Local=MaxPathTime | `max(tau_ij+0.008*x_ij/b_ij)` | 微秒；独占路径近似，不等同共享阶段时间 |

平滑目标使用固定参考 `H_ref=max初始H`：`Phi(H)=H_ref/beta * logsumexp(beta*H/H_ref)`，beta无量纲。空需求直接返回，不计算0/0。压力初版可定义 `pi_l=H_l/H_ref`，不开放任意用户表达式。这些是明确的候选定义，不是已证明最优的目标。

第一版融合规则：候选真实字节量至少达到 `rho×种子真实字节量`；有全局目标时先最大化其单位时间收益，平局时最小化局部代价；无全局目标则直接最小化局部代价。最终按固定匹配序打破平局。rho的配置值必须输出，范围(0,1]；其限制只是启发式防碎片约束，不提供整体不退化保证。

不把微秒、字节、微秒²直接加权求和。字典序中不同单位处于不同比较层级，无需伪造统一量纲。任何改成“允许全局损失epsilon再优化局部”的规则都须作为新选择规则显式登记，不能偷偷改变上述语义。

### 6.1 与研究清单的对应

| 原方案 | 接口落点 | 首轮状态 |
|---|---|---|
| S0 | Space::Bytes | 实现 |
| S1 | Space::SerializationTime | 在整数部分匹配路径通过后实现 |
| S2-A | Space::DelayAdjustedTime | 保留接口，后续独立验证 |
| S2-B | 无入口 | 暂缓 |
| S3 | 共享Topology、H与候选真实资源工作量 | 主路线，不单独建类 |
| S4 | 无入口 | 暂缓 |
| G0/G1/G2/G3 | GlobalRule及压力候选开关 | 分别实现，不假定所有组合都有效 |
| L0/L1/L2/L3 | LocalRule | 共用评分函数；L3不引入动态逐流带宽 |
| L4 | 无入口 | 范围外 |
| F0 | Engine::FastNative | 原生对照 |
| F1 | 无精确带权求解器入口 | 暂缓 |
| F2 | pressure_candidate + local_edits | 压力启发式初始匹配，再有限修改；不宣称精确优化G |
| F3 | 候选池与共同重评分 | 与F2共用循环，不再建一套Planner |

`validate_options()` 拒绝：FastNative搭配时间变换/拓扑候选；负参数；零候选预算；未实现枚举；无局部修改却设置修改预算等无作用配置。不把所有布尔项与枚举的笛卡尔积当作实验矩阵。

第一批命名预设只有：`fast-native`、`partial-endpoint`、`partial-topology`；随后增加 `partial-time`。预设只是 Options 的命名映射，输出完整选项，不新增算法类。候选上限、rho等属于实验参数，本文不替研究结果选定最优值。

## 7. 评价边界：统一执行规则，不冒充包级网络

每个阶段内所有活动流同时开始；该阶段全部完成后启动下一阶段。阶段为算法执行定义，无机内流水线、逐流动态释放、多路径、ACK/PFC/拥塞控制或跨阶段重叠。

当前统一评价公式复用 `Topology::evaluate_gpu()`：

```text
alpha = 活动真实路径的最大单向传播时间
serial = max_l(0.008 * 本阶段经过l的真实字节 / B_l_Gbps)
stage_time = alpha + serial
A2A_time = sum(stage_time)
```

这是静态容量工作量加传播的解析近似；`serial` 是容量工作量下界，`alpha+serial` 也不宣称是任意固定路径网络的精确完成时间。alpha与serial可能来自不同流/资源。不输出逐流真实完成时刻、精确队列或“实测链路空闲”。

允许报告资源容量占用比代理：`total_bytes_l*0.008/B_l/A2A_time`；它表达在该模型下的归一化工作量，不是时间线观测。第一版不把 `1-占用比` 命名为真实网络空闲比例。

评价编排与候选分数分离，但共用同一解析物理内核，因此不能称为独立网络模型交叉验证。候选使用StageTime时，评价不是独立物理证据；最终仍须比较完整计划，不能用单阶段分数代替整个操作收益。

## 8. 规划耗时、日志和构建

`bvn_main.cpp` 负责：读取纯文本需求/拓扑表与CLI选项、构建Topology、调用plan/validate/evaluate、输出JSON。首版使用简单带数量头的文本表，不新增JSON解析依赖。路径表逐项携带src/dst、单向传播、RTT、资源ID序列；拒绝重复或缺失的活动路径。

输出至少包含：配置与单位、输入标识、源码/编译信息、执行阶段数、分解步数、候选/修改次数、逐阶段真实流与资源字节、传播/序列化分项、A2A时间、守恒误差。

计时拆分：

- `topology_setup_us`：可跨请求复用的路径与静态缓存构建；单独报告。
- `planning_us`：本次需求准备、权重与H计算、匹配、候选评分、残余更新、输出Plan的全部成本。
- `validation_us`、`evaluation_us`：规划完成后的检查和重放，单列。
- 文件I/O与日志序列化不混入planning；所有方案采用同一口径。

候选评分时的拓扑调用属于planning，不能因为其名称是evaluate而扣除。首次运行与复用静态拓扑的重复运行分开说明。可以额外报告 `planning_us+A2A_time`，但其规划部分依赖运行机器，不能混称纯通信加速比。

`bvn-build` 链接：新入口、新算法文件、原生matrix/all2all、config和topology。不要链接旧 `test.cpp`、旧 `main.cpp`、Local/Global或研究目录的同名链接工厂。`bvn-check` 运行新增测试；原 `make check` 保持兼容。

## 9. 必须通过的对齐与验证

| 检查 | 要证明什么 |
|---|---|
| 原生适配与直接调用逐阶段对账 | 同环境、同单位下，p_sets顺序与系数未被适配器改变；纯虚拟步骤删除规则明确 |
| 每对真实需求守恒 | 无丢失、重复、超发；不能只核对总字节 |
| 部分匹配合法性 | 每阶段源、目的各至多出现一次，允许源同时作为另一路的目的 |
| 全零、单边、稀疏、非满匹配 | 不依赖补齐，不因找不到完整匹配而卡住 |
| 单位权重映射 | 相同候选规则下回到字节分解语义 |
| 路径资源手算 | 独立链路可并行、同向共享累加、反向独立、传播单位正确 |
| 原生整数边界与时间映射误差 | 溢出明确失败、数值容差可审计 |
| 预算与确定性 | 候选/修改次数守预算，相同输入与环境产生相同计划 |
| 不合法组合 | 明确拒绝，不静默退化成另一种方案 |
| 历史回归 | 原FAST程序和既有测试仍可运行；不覆盖已有用户修改 |

测试中的小矩阵可手工列出匹配以核对合法性，在线算法不枚举全部匹配。增广路处理的是无权合法匹配，不能把函数名称Hungarian误写成已实现最大权求解。

## 10. 实现顺序及本设计的承诺范围

1. 完成公共结构、独立入口、Topology接线、FastNative适配与统一验证/评价。
2. 完成Bytes空间的无补齐端点基线，验证阶段上界、守恒和原生对照。
3. 在同一循环加入压力候选、有限增删/交换、真实候选展开与受约束选择。
4. 单独加入SerializationTime空间及数值检查；之后再开放加性传播映射和平滑目标。
5. 上述基础成立后，编写小规模扫描配置和报告；不把未实现选项算作完成实验。

本次文档定义代码职责、组合接口和评价边界，不声称任何候选已经优于FAST。实现阶段的成果应是可直接调用的统一工具，以及逐项通过验证的策略；无需以新建类或新增目录数量体现方案数量。

## 11. 本次实现规格（2026-11-09 授权执行）

本节将前文接口草案收敛为当前实现合同；有差异时以本节为准。用户指定由 GPT-6.1-Sol、high 推理强度实现。主 agent 负责规格、测试场景设计、审查与最终验证。不开启大规模性能扫描，不提交或推送Git；原有代码注释及未提交修改全部保留。

### 11.1 精确公共接口与默认值

- 使用第3节的命名空间、结构名、字段名、四个公开函数，不加虚基类。`Options` 提供默认成员值：Partial、Bytes、None、None、pressure=false、edits=false、candidate_budget=1、edit_attempt_budget=0、rho=1、beta=0、delay_alpha=0、native_unit_bytes=1。
- `Plan` 增加 `Engine engine`，用于记录来源；所有计数初始化为0，评价总量初始化为0。`Validation.ok` 初始为false；成功后显式置true。
- `validate_options(const Demand&, const Topology&, const Options&)` 成功返回void；失败抛 `invalid_argument` 或 `overflow_error`。`plan()` 总会先调用它，不依赖CLI预检。
- `validate_schedule(const Demand&, const Plan&)` 对计划损坏返回 `ok=false` 和具体原因，不修改计划。验证非法端点、重复源/目的、非正/非有限字节、空执行stage、非法系数及逐对超发；最终检查每一对的累计量。Bytes要求整数且精确守恒；时间空间按第5.3节容差检查。
- `evaluate_schedule(const Topology&, const Plan&)` 检查阶段结构再计价；它没有D，故不负责证明与需求守恒。`bvn_main` 必须先验证再评价，验证失败退出非零。
- `Stage.coefficient` 使用long double，但 `Stage.flows` 的字节字段仍用现有double，输出计划即最终执行真值。内部扣减应与最终double发送量对账；不能只验证高精度影子而忽略输出舍入。每个限制边直接发送其全部真实残余，确保严格消除；其他边舍入不能产生超发。超容差失败，不能用无记录的尾部修补掩盖错误。
- 零需求返回空计划、零评价、零分解步数；仍校验配置和输入结构。支持图以真实残余严格大于0定义，不将某个绝对阈值以下的业务直接丢弃。
- `Space::Bytes` 配置中的正需求在处理过程中保持整数，原始总字节≤2^53；新工具矩阵大小N∈[1,64]，输入必须恰为N²项、对角为0，Topology要求 `top.n=N && top.g=1`。
- 检查所有非自环路径都有资源、资源容量为有限正数，传播与RTT有限非负；逐项检查路径ID、重复资源和端点范围。外部构造器可接受的缺失活动路径，必须在本工具中拒绝。

### 11.2 内部函数签名建议

以下函数定义在 `.cpp` 的匿名命名空间；可按清晰度合并，但不改变职责与行为。

```cpp
Plan plan_native(const Demand&, const Topology&, const Options&);
Plan plan_partial(const Demand&, const Topology&, const Options&);
Workspace prepare_residual(const Demand&, const Topology&, const Options&);
std::vector<int> generate_seed(const Workspace&, unsigned n);
std::vector<int> generate_pressure_candidate(
    const Workspace&, unsigned n, const Topology&);
Candidate materialize_and_score(const std::vector<int>&,
    const Workspace&, const Topology&, const Options&);
bool better_candidate(const Candidate&, const Candidate&, const Options&);
void consume_candidate(Workspace&, const Candidate&, unsigned n);
```

工作区只保存一次分解所需数组：需求残余、固定权重、辅助残余、H/H_ref、支持图和临时匹配。不构造N⁴边对冲突矩阵，不复制全部候选的历史，不要求可配置任意函数回调。

### 11.3 候选生成与更新的确定规则

1. 种子：源编号升序；邻居目的编号升序；普通Kuhn增广路求最大基数匹配，未匹配为-1。只用正残余边。拓扑不参与该种子；没有业务时不进入循环。
2. 压力候选：本轮 `pi_l=H_l/H_ref`；对边定义优先值 `priority_ij=sum_l(pi_l*0.008*R_ij/B_l)`。源按该行最大priority降序，邻居按priority降序，平局按编号升序；按此顺序进行同一Kuhn匹配。它是排序启发式，不是最大权求解。
3. 首先完整展开种子；有预算时展开不同于种子的压力候选。随后从当前最佳合法候选尝试局部修改：源编号升序删除一条边、按(src,dst)升序插入空闲端点边、按源对升序交换两个已匹配目的。修改后必须满足正支持和匹配合法性。删除最后一条边无效。
4. 一次扫描找到严格更优候选后可从新最佳匹配重启扫描；所有重启共用本轮剩余预算，不重置计数。无法改进或预算耗尽则停止。非法修改、重复修改也计入edit_attempt_budget；只有不同的合法非空匹配进入完整展开并计入candidate_budget。种子算一次候选。候选上限和修改上限均为每轮预算，Plan累计计数应可审计。
5. 真实服务门槛始终相对于本轮原始种子的真实字节，不随最佳候选变化；固定rho∈(0,1]。种子本身始终可回退。不得附加“新q必须等于旧q”的护栏。
6. 比较规则为第6节字典序：全局rate降序、局部cost升序、目的向量字典序。浮点平局使用 `64*epsilon(double)*max(1,abs(a),abs(b))`；最后排序保证确定性。没有全局/局部规则的端点基线只取种子，不接受无意义的候选配置。
7. 修改后的q必须从完整匹配重新计算，真实字节与资源负载均重新展开。只有最终被选候选才修改R/Z/H；所有候选试算只读残余。
8. H按真实消费字节的路径工作量递减；负浮点舍入只在容差内归零并可检查。`H_ref=max(initial H)` 固定不随阶段变化。平滑logsumexp使用减去最大值的稳定写法。T必须有限且正，不除以0。

### 11.4 本次支持的组合与预设

本次实现全部三个Space、四个GlobalRule、四个LocalRule的有效枚举，连同有限局部修改。S2-B、S4、L4、精确F1仍不实现。有效组合不声称都有研究价值。

| CLI预设 | Space | Global | Local | 压力候选/修改 | 候选/修改预算 | rho | beta/alpha |
|---|---|---|---|---|---|---|---|
| fast-native | Bytes | None | None | 关闭/关闭 | 1/0 | 1 | 0/0 |
| partial-endpoint | Bytes | None | None | 关闭/关闭 | 1/0 | 1 | 0/0 |
| partial-topology | Bytes | Pressure | StageTime | 开启/开启 | 8/32 | 0.5 | 0/0 |
| partial-time | SerializationTime | None | None | 关闭/关闭 | 1/0 | 1 | 0/0 |
| partial-delay | DelayAdjustedTime | None | None | 关闭/关闭 | 1/0 | 1 | 0/1 |

表中是可运行默认值，非最优实验参数。通过CLI可覆盖Global、Local、预算、rho、beta、alpha，以及压力候选/修改开关。只有SmoothResidual允许beta>0并要求beta有限正数；其他目标beta必须0。只有DelayAdjustedTime允许alpha非零并要求alpha≥0；其他空间alpha必须0。

关闭修改要求edit预算为0；打开修改要求预算>0且总候选预算≥2。压力候选开启也要求候选预算≥2。Global/Local都None时不允许压力候选或修改、要求候选预算1。FastNative只接受表中的算法设置，可改变精确计数单位；Partial要求native_unit_bytes=1。规则错误直接报错，不自动更改用户显式参数。Smooth目标被显式选择时，若未指定beta，CLI设置默认beta=4并在结果中记录。

### 11.5 CLI 与文件协议

可执行文件：`FAST/simulation/build/bvn_simulator`。

```text
bvn_simulator --input CASE --method METHOD [--repeat R]
  [--space bytes|time|delay]
  [--global none|max|smooth|pressure] [--local none|stage|square|path]
  [--pressure 0|1] [--edits 0|1]
  [--candidate-budget C] [--edit-budget E] [--rho X]
  [--beta X] [--delay-alpha X] [--unit-bytes U]
```

先应用method预设，再应用显式覆盖，最后统一校验。未知参数、重复参数、非法数值、非有限数值、缺失值均报错；`--help` 返回0。repeat默认1、范围[1,100]。stderr放诊断，stdout只输出一个JSON对象。

输入为以下严格token协议，不使用JSON解析库；允许任意空白，不支持注释和多余尾部token：

```text
BVN_CASE_V1
N 3
DEMAND
0 0 1
2 0 0
0 0 0
RESOURCES 2
0 wan:forward 8
1 wan:reverse 8
PATHS 6
0 1 5000 10000 1 0
0 2 5000 10000 1 0
1 0 5000 10000 1 1
1 2 0 0 1 0
2 0 5000 10000 1 1
2 1 0 0 1 1
```

该例仅示范格式，非正式拓扑。资源行依次为ID、唯一名称、Gbps；ID必须连续从0开始，名称限 `[A-Za-z0-9_.:-]+`。路径行依次为src、dst、单向传播us、RTT us、资源数、资源ID列表。必须恰好包含每一对不同端点的路径，不能重复；自环路径内部为空。需求在文件中始终是字节。

JSON固定首层字段：`schema_version=1`、`method`、`options`、`n`、`payload_model`、`demand_bytes`、`communication_us`、`serialization_sum_us`、`propagation_sum_us`、`execution_stages`、`decomposition_steps`、`candidate_evaluations`、`edit_attempts`、`max_pair_error_bytes`、`planning_samples_us`、`topology_setup_us`、`validation_us`、`evaluation_us`、`resource_total_bytes`、`stages`、`build_info`。

每stage输出 `coefficient`、`flows:[{src,dst,bytes}]`、`alpha_us`、`serial_us`、`total_us`、`resource_bytes`。输出足够精度以重建字节检查。`build_info` 包含编译器、C++标准与Makefile注入的git revision/dirty状态（没有git时明确unknown）。输出 `options` 包含全部Options字段，不能只输出预设名字。

repeat每次重新调用plan，不复用残余；拓扑只建立一次。首个计划用于验证/评价/输出，后续计划必须与首个在匹配与字节上保持确定一致；计时数据排除IO、重复比较与验证。无需人为睡眠或进程间并行。报告planning原始样本，不把最快一次冒充典型值。

### 11.6 明确验收用例与实施清单

1. 新接口测试先于实现运行，记录预期缺失接口导致的失败，然后完成实现。
2. 构造3端点需求 `[0,0,1;2,0,0;0,0,0]`：原生补齐分解步骤2、真实发送3字节；无补齐端点种子依次可完成全部需求，阶段数≤2。不要错误断言其一定一步完成（公共q仍取真实残余最小值）。
3. 需求只有0→1的1024字节，路径100Gbps、传播5us：唯一stage总时间5.08192us。
4. 两条端点不冲突的1000字节流共享同一100Gbps资源、传播均5us：同stage 5.16us；若资源独立则5.08us。方向用两个独立资源时同理，不自动把反向流合并。
5. 全零需求、N=1、无法完整匹配的稀疏需求、原生纯虚拟边、相同资源多流、错误路径、整数溢出、非整数Bytes计划、需求守恒但重复源/目的的非法计划均有断言。
6. 同带宽与零alpha加性修正：Time与Delay空间的输出语义一致；统一常数权重与Bytes在相同端点候选规则下产生相同匹配及真实量（允许时间浮点误差）。
7. 不同带宽、非常小和非常大传播、非均匀需求：所有有效目标组合守恒并有限终止。使用固定小规模矩阵而非性能扫描；修改预算至少覆盖一次可接受删除，确认主动部分匹配路径确实执行。
8. 可独立测试CLI：采用测试文件内写临时输入并调用CLI，或 `bvn-check` 附带Python标准库内联smoke测试；不为此增加第二套框架。检查JSON有效、repeat长度、未知/重复参数失败。
9. 最后运行 `make -C simulation bvn-check` 与已有 `make -C simulation check`，记录命令、通过数与问题。主agent复查新增文件及Makefile增量；不运行clean以免影响旧构建。

## 12. 子agent实施任务

**Goal：** 在现有FAST工作区实现第11节可运行工具与测试。

**Architecture：** 三个生产文件共享现有Topology和原生FAST分解。所有候选通过真实stage接口对齐；原生流水线不参与新工具。

**Tech Stack：** 现有C++11/g++/Make，CLI与JSON输出不引入依赖。

**Global Constraints：** 仅新增第1节三个生产文件和一个测试文件，修改现有Makefile的新增目标部分；保留其他工作；不commit、不push、不改旧算法/注释、不派生更多子agent。可在父agent指定的进度目录写实施报告。

**Review Focus：** partial候选合法与进展；time映射输出舍入守恒；native整数范围；预算计数和真实策略生效；CLI错误拒绝及可重复输出。

- [x] A. 写聚焦失败测试；实现公共结构、验证、FAST适配与Bytes端点种子。
- [x] B. 实现固定路径评价编排、内部评分、压力候选、有限增删交换、所有有效目标。
- [x] C. 实现Time/Delay映射，补齐单位权重、守恒与数值失败测试。
- [x] D. 实现文件协议、CLI/JSON、重复计时和Makefile目标；完成CLI smoke测试。
- [x] E. 运行新旧验证、自查diff；在实施报告中列出通过测试及任何未解决问题。

各步骤顺序执行，数学或接口矛盾向父agent报告；常规实现细节自行处理。本文是直接交付给实现者的完整规格，评审与后续实验场景由主agent负责。
