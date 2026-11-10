#pragma once

// ============================================================================
// GlobalScheduler：端到端仿真器，把"算法"和"模型"串起来。
//
// 输入：一组 LocalScheduler（每个源 server 一个），它们在构造时把
//       "每 GPU 发往每个 server 的量"汇总成 server_n × server_n 的
//       server 级流量矩阵——元素是每块发送 GPU 的代表发送量（通常是平衡后的
//       每 GPU 平均值），不是 server 间的总流量。
//
// 处理流程（pipeline2，benchmark 默认使用）：
//   1. 用 FastAll2All 把 server 级矩阵分解成置换集合 p_sets；
//   2. 先做一次 server 内平衡（balance），再为每个置换阶段生成 dispatch 计划；
//   3. 用 alpha-beta 模型估算总耗时：
//        总时间 = balance
//               + 第一段 server 间传输（与 server 内 intrinsic all-to-all 重叠）
//               + 后续每段的 max(上一段 dispatch, 本段 server 间传输)
//               + 最后一段 dispatch
//      bound = 所有服务器间传输时间之和（没有 server 内开销时的下界）；
//      ratio = (t - bound) / t = server 内开销未能被网络传输掩盖的比例。
//
// 三个 pipeline 版本：
//   pipeline()  —— OPTION 1：每个置换阶段都重新做一次 balance；
//   pipeline2() —— OPTION 2：只在开头 balance 一次，之后每阶段只 dispatch（默认）；
//   pipeline3() —— 同 pipeline2 的服务器内调度流程，但 server 内代价可以换成
//                  任意拓扑模型函数；它不做 scaling，也不使用 hooks。
// ============================================================================

#include <iostream>
#include <vector>
#include "matrix.h"
#include "local.h"
#include "all2all.h"
#include "config.h"
#include <functional>
#include <cstdint>

using namespace std;

namespace native_topology {class Topology; struct GpuStageCost;}

// pipeline 的返回结果。
//   t     ：模型估算的端到端时间（us）
//   ratio ：server 内开销未被 server 间传输掩盖的比例
struct pipeline_result_t{
    double t;
    double ratio;
};

// 研究用的可选回调集合（默认 nullptr，不启用插桩，走默认的分解/通信代价）。
// 用途：让上层研究代码可以替换分解/通信代价、测量各阶段耗时、
// 或者拿到每个源 server 的每块 GPU 在每个置换阶段实际发送的 payload。
// 单位说明：矩阵元素仍按原始单位计数，真实字节数 = 元素 × MBpu × 1e6。
struct pipeline_hooks_t {
    // 若为 true，pipeline2 不调用内置的 to_scaled_doubly_stochastic_matrix/decompose，
    // 而是把尚未分解的 app 交给 prepare 回调，由研究代码一次性完成分解与调度。
    bool defer_decomposition = false;
    // 分解钩子：参数为 server 级矩阵、当前待发 payload（按 (源 server, 目的 server, GPU) 展平）、
    // FastAll2All 实例。
    std::function<void(Matrix&, const vector<uint64_t>&, FastAll2All&)> prepare;
    // 通信代价钩子：参数为一个置换阶段及其 payload，返回该阶段 server 间传输耗时；
    // 为空时使用默认的 permutation_set_cost。
    std::function<double(const PermutationSet&, const vector<uint64_t>&)> communication;
    // 输出：各阶段耗时（us）
    double balance_us = 0, intrinsic_us = 0, native_decompose_us = 0;
    double padding_wall_us = 0; // 双随机补齐墙钟耗时，不包含分解CPU时间
    double planning_us = 0; // 从pipeline开始到balance/dispatch计划生成完毕的墙钟时间，不是CPU时间
    DecompositionStats decomposition_stats; // 仅显式set_decomposition时观测；cpu_us为进程CPU时间
    vector<double> dispatch_us;
    // 输出：分解得到的阶段、以及每阶段每块 GPU 实际发往网络的单元数
    vector<PermutationSet> stages;
    vector<vector<uint64_t>> payload_units;
};

class GlobalScheduler{
private:
    ScheduleMode schedule_mode_ = ScheduleMode::Native;
    const native_topology::Topology* topology_ = nullptr; // 只借用，调用者负责生命周期
    bool decomposition_configured_ = false;
    DecompositionOptions decomposition_options_;
    DecompositionStats decomposition_stats_;
    vector<uint64_t> collect_pending_units() const;
    native_topology::GpuStageCost topology_stage_cost(const PermutationSet&,
        const vector<uint64_t>& actual_payload_units,double MBpu) const;
    uint server_n;
    uint gpu_n;
    bool enable_scaling;              // 是否在分解前做矩阵缩放
    Matrix mat;                       // server 级矩阵（server_n × server_n，元素为每 GPU 代表发送量）
    vector<LocalScheduler*> locals;   // 每个源 server 一个局部调度器（不负责释放）
    enum INTER_LINK_TYPE inter_type;
    struct link_info_t inter_info;
    enum INTRA_LINK_TYPE intra_type;
    struct link_info_t intra_info;
    bool* if_approx;                  // [src_server*server_n + dst_server] 是否跳过 balance
public:
    void set_schedule(ScheduleMode mode,const native_topology::Topology* topology = nullptr);
    // 完整匹配策略仅接入pipeline2；可配Native拓扑计价，不可叠加旧拓扑奖励或覆盖回调。
    void set_decomposition(const DecompositionOptions&);
    const DecompositionStats& decomposition_stats() const {return decomposition_stats_;}
    // 构造时会对每个 local 调用 prepare_load_balance()，并决定每个
    // (源 server, 目的 server) 组合是否需要平衡，然后汇总成 server 级矩阵。
    GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, INTER_LINK_TYPE type = INFB, INTRA_LINK_TYPE itype = DGX2, bool scaling = false, double MBpu = 1.0);
    GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, struct link_info_t _inter_link, struct link_info_t _intra_link, bool scaling = false, double MBpu = 1.0);
    ~GlobalScheduler();
    struct link_info_t get_link_info(){return inter_info;}
    // 旧方案：每个置换阶段重新 balance，再 dispatch；server 内用 spread_out 计价。
    struct pipeline_result_t pipeline(double MBpu = 1.0);
    // 默认方案：只 balance 一次，然后逐阶段 dispatch；可选 hooks 做研究插桩。
    struct pipeline_result_t pipeline2(double MBpu = 1.0, scaling_success_condition fn = &balance_alpha_beta, pipeline_hooks_t* hooks = nullptr);
    // 与 pipeline2 的 balance/dispatch 流程相同，但 server 内代价使用自定义拓扑函数
    // （评估不同互连拓扑；不做 scaling、不支持 hooks）。
    struct pipeline_result_t pipeline3(double MBpu = 1.0, intra_transfer_topo_fn fn= &intra_transfer_full_mesh);
    // 一个置换阶段的 server 间传输代价：alpha + freq*beta*MBpu（所有 server 并行）。
    double permutation_set_cost(uint freq, double MBpu = 1.0);
};
