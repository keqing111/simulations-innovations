#pragma once
#include "all2all.h"
#include "model.h"

namespace native_topology {
bool force_edge(const std::vector<unsigned>& residual,unsigned n,std::vector<int>& map,unsigned row,unsigned col);
class Planner {
 public:
  Planner(const Topology& topology,std::vector<uint64_t> rows,std::string mode);
  std::vector<PermutationSet> decompose(Matrix& service);
  uint64_t remaining_inter_bytes() const;
  uint64_t cycle_trials=0,changed_matches=0,repair_matches=0;
 private:
  const Topology& top;
  std::vector<uint64_t> remaining;
  std::string mode;
  void adjust(Matrix& residual,hungarian_info_t& info);
};
}
