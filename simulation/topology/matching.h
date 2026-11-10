#pragma once
#include "all2all.h"
#include "model.h"

// ============================================================================
// topology/matching：在 Birkhoff 每一轮内部修改完美匹配，使阶段计划更贴合
// 资源模型。与 scan/ 的区别：
//   - scan 暴露通用 choose() 回调，比较多种启发式；
//   - topology 只提供有限的已知模式（见 mode），并用 force_edge 做局部修补。
// 两者最终都走 FastAll2All::decompose_with 的 adjust 钩子，因此不改变
// 原生分解/扣减流程，只改变“同一系数下选哪条置换”。
// ============================================================================

namespace native_topology {
// 尝试把 (row -> col) 强制放进当前完美匹配 map：
// 若 col 未被占用直接改；否则沿交替路径/环回溯，成功返回 true。
// residual 用于确认边存在（容量 >0）；失败时不保证 map 不变。
bool force_edge(const std::vector<unsigned>& residual,unsigned n,std::vector<int>& map,unsigned row,unsigned col);
// 顶层规划器：接收 balance 后的逐 server 真实行和，调用 decompose_with，
// 并在每轮 adjust 中按 mode 调整匹配。
//   wan-repair   : 只修复共享 WAN 的明显冲突；
//   link-cost    : 用完整资源代价打分选择；
//   link-cost-wan: 同上但强化 WAN 修复；
//   maxmin       : 精确求最大公共系数 q*，仅用资源代价做平局判定。
class Planner {
 public:
  Planner(const Topology& topology,std::vector<uint64_t> rows,std::string mode);
  // service 会被就地分解；返回原生 PermutationSet 序列（带原始单位频次）。
  std::vector<PermutationSet> decompose(Matrix& service);
  // 所有阶段结束后仍未覆盖的真实 server 间字节（应为 0）。
  uint64_t remaining_inter_bytes() const;
  // 审计计数：尝试的修补次数、实际换掉的匹配数、成功修补数。
  uint64_t cycle_trials=0,changed_matches=0,repair_matches=0;
 private:
  const Topology& top;
  std::vector<uint64_t> remaining; // 逐 GPU 真实剩余，索引 [(src*n+dst)*g+u]
  std::string mode;
  // decompose_with 每轮开始时的回调：residual 是当前 server 级残余矩阵，
  // info 是原生匈牙利给的匹配；实现可直接改 info.matching。
  void adjust(Matrix& residual,hungarian_info_t& info);
};
}
