#pragma once
#include "planner.h"

// ============================================================================
// independent/policy.h：两条互相独立的“单目标”拓扑策略接口。
//
// 这一轮刻意不把 scan/ 中所有 G/L/R 因子组合成一个联合评分，而是分别验证：
//   bandwidth —— 只最小化当前阶段的“最大共享资源串行化时间”；
//   time      —— 只最小化当前阶段的“最大单路径完成时间” alpha + x/B。
// 两者都从本轮种子匹配出发，并保持同一组安全护栏：
//   * 候选仍然是 perfect matching；
//   * 候选的最小系数必须恰好等于种子系数 q；
//   * 候选承载的真实跨服务器字节不得少于种子；
//   * 只有当前阶段目标严格改善才替换种子，否则回退。
//
// “maxmin-” 前缀表示先用精确 Max-Min 求 q* 作为种子，再用同一策略构造；
// 它用于把“粒度收益”和“拓扑策略收益”分开，而不是两种策略的联合。
//
// 重要边界：局部接受只保证当前阶段目标不退化，不保证后续残余更简单、
// 阶段数不变或总 CCT 单调下降；真实网络字节守恒由 consume() 和
// decompose_into() 末尾的检查共同保证。
// ============================================================================

namespace independent_fast {
// 一次分解的累计诊断计数，主要用于确认策略确实执行了构造/接受/拒绝，
// 以及确认审计中的候选预算没有被悄悄放大。
struct Counters {
    uint64_t stages=0, matching_calls=0, constructions=0, accepted=0;
    uint64_t coefficient_rejections=0, volume_rejections=0;
};

// 在支持图 a>=q 上求“最小化最大边代价”的完美匹配。
//   a[s*n+d] 是当前 server 级残余容量；neighbors[s] 是可用的目的 server；
//   costs[s*n+d] 是 time 策略的辅助单路径完成时间。
// 返回每个源 server 的目的 server；若支持图不存在完美匹配则抛异常。
// 实现是对排序后的候选代价做二分，再用增广路检查可行性，因此是多项式时间，
// 不枚举排列；calls 记录可行性探测次数，供审计/计时解释。
std::vector<int> minimum_bottleneck(const std::vector<unsigned>& a,
    const std::vector<std::vector<unsigned>>& neighbors, unsigned q,
    const std::vector<double>& costs, uint64_t& calls);

// 一个需求实例的独立分解器。
// 构造参数 rows 是 balance 之后的逐 GPU 真实剩余需求，索引
// [(src_server*n+dst_server)*g+u]；method 同时决定种子类型与单目标策略。
// 内部会保存并逐阶段扣减 remaining，因此同一个 Planner 不能重复调用
// decompose_into()：第二次调用时 remaining 已为空，会触发守恒检查。
class Planner {
public:
    Planner(const native_topology::Topology&, std::vector<uint64_t> rows, std::string method);
    void decompose_into(FastAll2All&);
    const Counters& stats() const { return counts; }
private:
    const native_topology::Topology& top;
    std::vector<uint64_t> remaining; // 真实逐 GPU 剩余；虚拟补齐不写入这里
    std::string objective;           // native / bandwidth / time（maxmin- 前缀已剥离）
    bool exact=false;                // 是否先求精确 q* 作为种子
    // 仅在 exact=true 时创建；复用 scan/ 中已经验证过的精确 Max-Min 规划器。
    std::unique_ptr<native_scan::Planner> threshold_planner;
    // bandwidth 策略复用的资源缓存：路径预展开为整数资源 id，并复用临时数组。
    std::unique_ptr<native_scan::ResourceCache> resources;
    // time 策略的静态路径最小带宽：path_bandwidth[src*n+dst] = min_{资源 e∈路径} B_e。
    std::vector<double> path_bandwidth;
    Counters counts;
    // 把当前匹配和系数 q 变成“每个 server 对实际搬运的真实字节”的流列表；
    // 只统计 src!=dst 的真实剩余，所以虚拟补齐不会被算成网络服务。
    std::vector<native_topology::Transfer> flows(const std::vector<int>&, unsigned) const;
    // 按选中的 matching 与系数 q 扣减 remaining；每个 GPU 扣 min(q, 剩余)。
    void consume(const std::vector<int>&, unsigned);
};
}
