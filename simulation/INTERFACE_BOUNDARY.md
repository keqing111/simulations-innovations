# FAST 代码归并：文件、类与接口边界

状态：接口已归并到主项目；构建与验证说明见 [README.md](README.md)。

本文定义代码职责、数据归属、接口、调用与兼容边界，不讨论算法合理性。代码块采用头文件声明形式；未列出的原有成员继续保留，不能用这些片段整段替换现有头文件。

## 1. 本次归并范围

- 新策略实现归入 `FastAll2All`，不再新增独立的 `Planner` 类。
- 整理现有 `native_topology::Topology`，不另建第二套拓扑类。
- `LocalScheduler` 的头文件和实现均不修改。
- `GlobalScheduler` 只增加配置、数据传递、分解调用和网络计价接线。
- `PermutationSet` 保留原有结果结构，不加入匹配工作区。
- 不新增逐流动态带宽、流完成事件、路由选择或新的执行同步机制。
- 正式构建只编译主项目源码，不依赖历史 `results/.../source`。
- 本次不删除历史目录、不推送GitHub；历史归档和本地清理另行执行。

## 2. 文件级修改清单

下表路径均相对于 `simulation/`。

|文件|动作|负责内容|
|---|---|---|
|`algorithm/all2all.h`|修改|声明策略枚举、新分解重载、匹配工作区与私有辅助函数|
|`algorithm/all2all.cpp`|修改|实现两条策略及其公共接入，复用原生分解循环|
|`topology/model.h`|修改|声明GPU固定路径、整数资源索引和GPU传输计价接口|
|`topology/model.cpp`|修改|实现拓扑预处理、查询和统一阶段计价|
|`scheduler/global.h`|修改|声明策略配置接口和内部接线函数|
|`scheduler/global.cpp`|修改|收集真实待发量，调用A2A，使用实际派发量计价|
|`main.cpp`|按新入口需要修改|选择策略、创建拓扑、调用Global；无参数默认入口保留|
|`test.cpp` / `test.h`|按测试入口需要修改|保留原benchmark，增加或转接新模式入口|
|`Makefile`|修改|将主目录拓扑实现纳入编译，补齐头文件依赖|
|`tests/test_topology_schedule.cpp`|新增测试文件|直接测试归并后的接口、兼容性和结果一致性|
|`scheduler/local.h/.cpp`|不改|保持数据准备、balance、dispatch及现有读取接口|
|`algorithm/matrix.h/.cpp`|不改|保持矩阵存储和运算|
|`scheduler/config.h/.cpp`|不改|保持原有链路工厂及参数接口|

不新增核心算法文件，不把 `independent/main.cc` 的JSON、计时和归档代码复制进 `all2all.cpp`。从历史 `scan` 模块只迁移当前两条策略实际需要的函数，不整份引入历史策略集合。

## 3. `topology/model.h`：静态拓扑与阶段计价

### 3.1 新增的数据声明

```cpp
namespace native_topology {

struct ResourceInfo {
    std::string name;               // 展示名称，不在匹配热路径中查字符串
    double bandwidth_gbps;
};

struct PathInfo {
    std::vector<unsigned> resource_ids; // 资源ID就是资源数组下标
    double propagation_us;              // 显式提供的单向传播时间
    double rtt_us;                      // 静态RTT，不由查询接口隐式换算
};

struct GpuTransfer {
    unsigned src_gpu;               // 全局物理GPU编号
    unsigned dst_gpu;
    double bytes;                   // 从原生数据单位换算后的实际字节
};

struct GpuStageCost {
    double alpha_us = 0;
    double serial_us = 0;
    double total_us = 0;
    std::vector<double> resource_bytes; // 按资源ID索引，保留浮点字节
};

} // namespace native_topology
```

全局GPU编号统一为 `server_id * gpu_n + local_gpu_id`。

使用 `GpuTransfer`，而不是改变现有 `Transfer` 的端点含义：旧 `Transfer` 仍表示服务器对，避免旧实验代码发生无编译错误的语义变化。

`bytes` 使用 `double` 是为了兼容原生可配置 `MBpu` 的单位换算；真实需求和派发守恒仍在原生整数数据单位中核对，不在浮点字节上判断精确相等。

新增 `GpuStageCost`，不将浮点字节写入旧 `Cost::bytes` 的整数计数表。旧 `Cost` 的字段类型保持不变；新结果需要名称时通过资源ID查询，不在核心结果中重复保存资源名称。

### 3.2 `Topology` 新增接口与成员

```cpp
class Topology {
public:
    // 下列为新增接口；现有构造函数及旧查询/计价接口保留。

    // 输入：服务器数、每机GPU数、资源数组、所有GPU对的固定路径。
    // paths按 src_gpu * rank_count + dst_gpu 展平。
    // 输出：完成校验与缓存构建的拓扑实例。
    static Topology from_fixed_routes(
        unsigned server_n,
        unsigned gpu_n,
        const std::vector<ResourceInfo>& resources,
        const std::vector<PathInfo>& paths);

    // 只读查询，返回内部引用，不复制资源列表。
    const PathInfo& gpu_path(unsigned src_gpu, unsigned dst_gpu) const;
    const ResourceInfo& resource(unsigned resource_id) const;
    unsigned resource_count() const;

    // 查询构造时已经缓存的路径最小带宽。
    double path_min_bandwidth_gbps(unsigned src_gpu, unsigned dst_gpu) const;

    // 输入：本stage实际物理GPU传输列表。
    // 输出：GPU阶段计价，资源字节保留double，不截断到旧Cost整数表。
    GpuStageCost evaluate_gpu(const std::vector<GpuTransfer>& transfers) const;

private:
    std::vector<ResourceInfo> resources_;
    std::vector<PathInfo> gpu_paths_;
    std::vector<double> path_min_bandwidth_gbps_;
    bool legacy_server_view_; // 旧构造函数为true，from_fixed_routes为false
};
```

以上类位于 `namespace native_topology`；声明片段省略了现有 `n`、`g` 等成员。

### 3.3 接口约束

- 现有多DC构造函数继续可用，由内部初始化逻辑补齐新的GPU路径缓存。
- 现有 `path()`、`path_alpha()`、`evaluate(vector<Transfer>)` 和服务器级端点语义保留，不在本次删除或重命名。
- 旧构造函数创建的实例继续支持旧服务器级接口。`from_fixed_routes()` 创建的是GPU路径实例，调用旧服务器级路径查询或服务器级计价时抛出 `std::logic_error`；不任意挑选某块GPU路径作为服务器代表。通用GPU路径实例统一使用新接口。
- 新接口只处理固定路径，不选择路由，不读取调度残余，不保存匹配状态。
- 所有静态缓存在创建拓扑时建立；配置给调度器后不得修改拓扑。
- `evaluate_gpu()` 不修改拓扑，不维护跨stage状态，不产生逐流事件。
- 自环、零真实量不产生跨机网络费用；本接口的正式调用方只提交跨机传输。
- 空传输列表返回零费用；非法GPU编号、非法资源ID、非有限/非正带宽、非法时延或字节量报错。
- 拓扑信息不完整时明确报错，不静默退回某个默认链路。

## 4. `algorithm/all2all.h`：策略与分解边界

### 4.1 策略枚举

直接在 `all2all.h` 中声明，不新增配置头文件。

```cpp
enum class ScheduleMode {
    Native,
    BandwidthAware,
    CompletionTimeAware
};
```

没有联合策略、其他历史方法名或算法注册系统。三种模式共用原生分解流程。

### 4.2 扩展 `hungarian_info_t`

```cpp
struct hungarian_info_t {
    // 原有字段，保持原语义。
    std::vector<int> matching;
    std::vector<bool> visit;
    std::vector<std::unordered_set<uint>> row_to_col;

    // 新增：本次匹配构造的可复用工作区。
    std::vector<int> seed_matching;
    std::vector<int> candidate_matching;
    std::vector<int> inverse_matching;
    std::vector<unsigned char> candidate_visit;
    std::vector<double> resource_load_bytes;
    std::vector<double> edge_cost_us;
    std::vector<double> cost_thresholds_us;
};
```

字段约定：

- 原有 `matching` 继续用两侧顶点编号：行 `0..N-1`，列 `N..2N-1`。
- `seed_matching` 和 `candidate_matching` 用紧凑的源服务器到目的服务器编号，长度N，值为 `0..N-1`；写回原结构时统一转换。
- `inverse_matching` 和 `candidate_visit` 是新候选构造的临时状态，不污染原生DFS状态。
- `resource_load_bytes` 长度为资源数；`edge_cost_us` 按服务器对展平，长度N×N。
- 容器按需分配并复用，不为Native默认路径强制创建全部拓扑工作数组。
- 这里只保存工作区，不保存静态路径副本，也不保存整次通信的真实剩余矩阵。

具体实现可在此结构内部补充增广回溯所需的临时数组，但不增加新的外部接口或改变上述字段含义。

### 4.3 `FastAll2All` 公开接口

```cpp
class FastAll2All {
public:
    // 原有接口继续保留。
    void decompose();
    void decompose_with(
        const std::function<void(Matrix&, hungarian_info_t&)>& adjust);

    // 新增重载；不改变原有decompose()的默认行为。
    void decompose(
        ScheduleMode mode,
        const native_topology::Topology& topology,
        const std::vector<uint64_t>& pending_units);

    // 原有输出继续使用，不新增另一套调度结果类。
    std::vector<PermutationSet> p_sets;

private:
    // 新增：一次分解内持续更新的真实剩余量。
    std::vector<uint64_t> remaining_units_;

    // 以下是内部操作，不对main/Global公开。
    void adjust_matching(ScheduleMode mode,
                         const native_topology::Topology& topology);

    bool match_bandwidth(const native_topology::Topology& topology,
                         uint q_scaled,
                         std::vector<int>& candidate);

    bool match_completion_time(const native_topology::Topology& topology,
                               uint q_scaled,
                               std::vector<int>& candidate);

    bool accept_candidate(ScheduleMode mode,
                          const native_topology::Topology& topology,
                          uint q_scaled);

    void build_transfers(const std::vector<int>& matching,
                         uint q_scaled,
                         std::vector<native_topology::GpuTransfer>& out) const;

    void consume_pending(const std::vector<int>& matching, uint q_scaled);
};
```

### 4.4 输入、输出与生命周期

|项目|约定|
|---|---|
|`pending_units`|balance后的真实待发量，长度N×N×G，索引 `[(src_server*N+dst_server)*G+src_gpu_local]`|
|输入单位|原生数据单位，不要求调用方提前转成字节|
|单位换算|`FastAll2All` 使用原有 `MB_per_unit`；内部 `q_scaled` 先恢复缩放，再用于真实待发量与字节计算|
|可变性|复制到 `remaining_units_` 后修改，不改变调用方数据，更不修改Local内部需求|
|拓扑|只借用只读引用，分解期间有效；分解完成后不保留其地址|
|前置条件|与原生接口一致，调用前完成矩阵补齐；新重载不隐式再次补齐或启用缩放|
|输出|继续通过 `p_sets` 返回置换阶段；不返回新Planner或另一套流程|
|异常|维度、单位、缩放溢出、路径或输入不合法时明确失败|

`Native` 在新重载下仍选择原生匹配；提供拓扑不会自动将它改成拓扑感知策略。旧 `decompose()` 不要求提供拓扑和真实待发量。

`build_transfers()` 的输出端点按照原生跨机传输规则映射：源服务器s的GPU u到目的服务器d的同编号GPU u。最终目的GPU对齐仍由原生dispatch处理，本次不引入新的GPU通道选择。

### 4.5 `PermutationSet` 的边界

本次不增加必需字段，也不修改原有字段与接口：

```cpp
class PermutationSet {
    // 原有frequency、scaling_factor、dim继续保留。
public:
    std::map<uint, uint> mp;
    uint get_freq() const;
    void set_freq(uint freq);
    // 原有构造函数和其他成员不变。
};
```

它只表达“服务器匹配 + 公共发送量”。不保存Topology、DFS工作区、真实剩余矩阵、候选历史或动态带宽。

阶段实际payload和计价结果属于执行/观测数据，继续由Global及现有hooks记录，不在本次复制进每个 `PermutationSet`。

## 5. `scheduler/global.h`：配置和接线

### 5.1 配置接口与成员

```cpp
class GlobalScheduler {
public:
    // 新增配置入口；不接管topology所有权。
    void set_schedule(ScheduleMode mode,
                      const native_topology::Topology* topology = nullptr);

    // 原有签名不变。
    pipeline_result_t pipeline2(
        double MBpu = 1.0,
        scaling_success_condition fn = &balance_alpha_beta,
        pipeline_hooks_t* hooks = nullptr);

private:
    ScheduleMode schedule_mode_ = ScheduleMode::Native;
    const native_topology::Topology* topology_ = nullptr;

    // 读取现有Local::pending_units()，不新增Local接口。
    std::vector<uint64_t> collect_pending_units() const;

    // 实际派发结果转成物理GPU传输，并调用统一拓扑计价。
    native_topology::GpuStageCost topology_stage_cost(
        const PermutationSet& stage,
        const std::vector<uint64_t>& actual_payload_units,
        double MBpu) const;
};
```

Global不保存两条新匹配算法，不再从历史实验目录创建独立Planner。A2A实例仍由原有pipeline流程创建，本次不要求改为Global的长期成员。

### 5.2 配置行为

|配置|分解行为|网络计价|
|---|---|---|
|未调用 `set_schedule()`|原生|原有alpha-beta|
|`Native, nullptr`|原生|原有alpha-beta|
|`Native, &topology`|原生|公共拓扑计价|
|`BandwidthAware, &topology`|公共资源策略|同一公共拓扑计价|
|`CompletionTimeAware, &topology`|完成时间策略|同一公共拓扑计价|
|新策略但拓扑为空|配置错误|不运行|

新配置首先支持 `pipeline2()`。已有 `pipeline()`、`pipeline3()` 保留原接口与默认行为；若设置非默认配置后调用尚未接入的pipeline，明确报不支持，不能静默忽略配置。

### 5.3 `pipeline2()` 内部改动边界

1. 默认路径保留原有顺序和行为。
2. 启用正式拓扑模式后，仍执行原有balance；在balance完成后读取真实待发量并调用A2A新重载，再进行原有dispatch。
3. 获取每阶段每源GPU的实际派发量。此数据在正式拓扑模式下必须可用，不能依赖调用方额外开启hooks。
4. `actual_payload_units` 长度N×G，按 `src_server*G+src_gpu_local` 索引；目的服务器来自阶段 `mp`，实际跨机GPU映射与A2A一致。
5. 网络费用来自 `topology_stage_cost()`；机内费用、pipeline组合与 `pipeline_result_t` 的含义保持不变。
6. 全局策略不查询或修改Local私有数组，不重复执行balance，不在A2A内调用dispatch。

### 5.4 hooks兼容约定

- 不启用正式拓扑模式时，现有hooks行为保留，历史研究入口继续可用。
- 正式拓扑模式下可以使用只读观测hooks记录阶段和费用。
- 若同时提供会替换分解的 `prepare/defer_decomposition`，或替换计价的 `communication`，明确拒绝冲突，不允许执行顺序决定哪个配置覆盖另一个。
- 不删除已有hooks，不将它们作为正式策略的必填参数。

## 6. `LocalScheduler`：明确不修改

保持以下现有接口及实现：

```cpp
void prepare_load_balance();
bool check_balance_dispatch(uint to_server_id, double MBpu = 1.0);
void server2server_approx(uint to_server_id);
void server2server_balance(uint to_server_id, load_balance_result r);
void server2server_dispatch(uint to_server_id, load_balance_result r, uint freq);
uint64_t pending_units(uint to_server_id, uint gpu_id) const;
```

不传入Topology，不增加策略选项，不修改balance/dispatch，不改变剩余数据的所有权。Global只通过已有公开方法调用它。

## 7. 类之间的数据所有权

|对象/数据|所有者|谁可以修改|
|---|---|---|
|拓扑实例与固定路径缓存|main或上层调用者|完成初始化后视为只读|
|Local真实待发数据|各Local实例|仅原有Local方法|
|传给A2A的待发量快照|Global本次调用|A2A接收const引用后复制|
|A2A真实剩余影子|FastAll2All|A2A分解内部|
|匹配工作区|FastAll2All内的hungarian_info_t|A2A匹配函数|
|分解结果p_sets|FastAll2All|分解期间写，随后Global读取|
|阶段实际派发量|Global本次pipeline调用|Global记录，计价器只读|
|阶段费用|计价调用返回值/Global观测输出|不反向修改拓扑或原始需求|

`set_schedule()` 借用的Topology必须覆盖Global使用该配置的整个期间。不能传临时对象地址；拓扑重建后应重新配置。Local、Global、A2A不互相接管原有对象的释放职责。

## 8. 主文件调用示例

以下为接口使用示意，省略原有Local创建、资源定义和矩阵输入。

```cpp
auto topology = native_topology::Topology::from_fixed_routes(
    server_n, gpu_n, resources, fixed_gpu_paths);

GlobalScheduler scheduler(server_n, gpu_n, locals,
                          ETHER400, H100, false, MBpu);

// 三种策略只切换这里；拓扑和评价接口不变。
scheduler.set_schedule(ScheduleMode::BandwidthAware, &topology);
auto result = scheduler.pipeline2(MBpu);
```

独立运行每种模式时，均从同一份原始需求重新创建Local/Global，不连续复用已经被dispatch消耗的Local实例。

旧主文件无需迁移为新调用：不设置策略时，原来创建Local/Global并调用 `pipeline2()` 的代码继续有效。

## 9. 依赖与编译边界

```text
main / test
    → GlobalScheduler
        → LocalScheduler（不改）
        → FastAll2All（原生及两种新策略）
        → Topology（配置与实际阶段计价）

FastAll2All → Topology（只读查表与候选计价）
Topology   → 不依赖Global、Local、FastAll2All或PermutationSet
```

- `all2all.h` 需要的Topology引用和GpuTransfer类型使用前置声明；完整定义在 `.cpp` 中包含，避免循环依赖。
- `global.h` 对Topology与GpuStageCost采用前置声明；完整实现放在 `.cpp`。
- `model.h` 只依赖标准库，不包含调度器头文件。
- Makefile编译 `topology/model.cpp`，补齐对象文件和头文件依赖。
- 核心构建不链接 `independent/policy.cpp`、`scan/planner.cpp`、`topology/matching.cpp` 或任何 `results` 源码。
- 现有Topology旧接口为历史工具保留；历史工具并非正式构建的算法依赖。

## 10. 完成归并的验收边界

1. 原项目默认入口、原生分解与pipeline2继续可用；默认输出与迁移前基线一致。
2. 三种模式能够通过同一主项目构建产物调用。
3. 对原有同构GPU路径配置，两条新策略的阶段匹配、系数、实际派发量与费用，与归并前对应实现一致；新增GPU差异路径另列接口测试，不混作等价迁移。
4. Local、矩阵实现和用户批注不被覆盖；修改原有函数时保留相邻提问和回答。
5. GPU/资源索引、payload维度、单位及缩放转换错误会被拒绝；不静默改变端点或单位含义。
6. Topology查询不逐stage重建路径；动态工作数组在A2A内部复用，不复制进PermutationSet。
7. 正式策略不要求外部hooks；旧hooks默认行为保留，正式模式下的覆盖冲突明确报错。
8. 不新增逐流动态仿真、其他策略或独立Planner，不以本次归并为由调整算法目标与执行依赖。

实际实现另在 `hungarian_info_t` 内复用稀疏服务器对资源贡献 `edge_loads`、每层增广的 `reduced_loads` 和候选排序 `candidates`；在A2A内增加私有的 `prepare_topology_stage()` 与 `original_units()`。这些均不对外暴露、不写入PermutationSet，也不新增类文件。

本文件确认的是上述代码边界。历史GitHub归档、数据清理和算法调整不因本次归并自动开始。
