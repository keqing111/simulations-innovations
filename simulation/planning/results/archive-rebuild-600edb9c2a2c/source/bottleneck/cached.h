#pragma once
#include "matching.h"
#include <memory>
namespace native_topology {
struct CostSummary {double us;uint64_t bytes;};
class CachedScorer {
 public:
  explicit CachedScorer(const Topology&);
  CostSummary score(const std::vector<int>& matching,unsigned quota,const std::vector<uint64_t>& remaining);
 private:
  unsigned n,g;
  std::vector<std::vector<unsigned>> routes;
  std::vector<double> alpha,bandwidth;
  std::vector<uint64_t> loads;
};
void set_variant(const std::string&);
class CachedPlanner {
 public:
  CachedPlanner(const Topology&,std::vector<uint64_t>,std::string);
  std::vector<PermutationSet> decompose(Matrix&);
  uint64_t remaining_inter_bytes()const;
  uint64_t cycle_trials=0,changed_matches=0,repair_matches=0;
 private:
  const Topology& top;
  std::vector<uint64_t> remaining;
  std::string variant;
  std::unique_ptr<Planner> delegate;
  std::unique_ptr<CachedScorer> scorer;
  void adjust(Matrix&,hungarian_info_t&);
};
}
