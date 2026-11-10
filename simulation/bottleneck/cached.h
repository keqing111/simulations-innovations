#pragma once
#include "matching.h"
#include <memory>

// ============================================================================
// bottleneck/ 是对 topology::Planner 的“瓶颈开销”实验实现。
//
// 三种变体通过 set_variant() 或环境变量 FAST_BOTTLENECK_VARIANT 选择：
//   reference —— 直接委托给 topology::Planner（慢，但作为数值基准）；
//   cached    —— 用 CachedScorer 预展开资源 id 并复用 load 数组，
//                避免每轮构造字符串路径/map；结果应与 reference 等价；
//   bounded   —— 在 cached 基础上只考察真实剩余最多的 2 条候选边，
//                并拒绝降低公共系数的候选（有界搜索）。
//
// 注意：bottleneck 只覆盖 link-cost 模式；其他 mode 始终交给 reference。
// ============================================================================

namespace native_topology {
// 与 scan::CostSummary 同形：本阶段真实字节与实际耗时（us）。
struct CostSummary {double us;uint64_t bytes;};
// 缓存版拓扑打分器。构造时把每个 server 对路径翻译成带宽数组下标，
// 之后 score() 只做数组累加，不再访问 map/字符串。
class CachedScorer {
 public:
  explicit CachedScorer(const Topology&);
  // matching 必须是 n 元置换；quota 是本阶段公共系数 w；
  // remaining 是逐 GPU 真实剩余 [(src*n+dst)*g+u]。
  CostSummary score(const std::vector<int>& matching,unsigned quota,const std::vector<uint64_t>& remaining);
 private:
  unsigned n,g;
  std::vector<std::vector<unsigned>> routes; // server 对 -> 资源 id 序列
  std::vector<double> alpha,bandwidth;       // 每条 server 对的延迟 / 各资源带宽
  std::vector<uint64_t> loads;               // 每次 score 复用，不能并发调用
};
// 全局设置默认变体；环境变量在 CachedPlanner 构造时优先于它。
void set_variant(const std::string&);
// 具有与 topology::Planner 相同外部接口的实验规划器。
class CachedPlanner {
 public:
  CachedPlanner(const Topology&,std::vector<uint64_t>,std::string);
  std::vector<PermutationSet> decompose(Matrix&);
  uint64_t remaining_inter_bytes()const;
  uint64_t cycle_trials=0,changed_matches=0,repair_matches=0;
 private:
  const Topology& top;
  std::vector<uint64_t> remaining; // 逐 GPU 真实剩余
  std::string variant;
  std::unique_ptr<Planner> delegate; // reference（或非 link-cost）实际执行者
  std::unique_ptr<CachedScorer> scorer; // cached/bounded 使用的打分器
  // cached/bounded 的每轮匹配调整，逻辑与 topology::Planner::adjust 类似，
  // 但候选数和系数守卫由 variant 控制。
  void adjust(Matrix&,hungarian_info_t&);
};
}
