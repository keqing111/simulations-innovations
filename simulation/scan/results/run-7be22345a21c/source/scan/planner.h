#pragma once
#include "matching.h"
#include <memory>

namespace native_scan {
struct Options {
    bool granularity=false,link=false,residual=false,exact=false;
    static Options parse(const std::string& method);
};
struct CostSummary {double us=0;uint64_t bytes=0;};
class ResourceCache {
public:
    explicit ResourceCache(const native_topology::Topology&);
    CostSummary score(const std::vector<int>&,unsigned,const std::vector<uint64_t>&);
    std::vector<double> priorities(const std::vector<uint64_t>&);
private:
    unsigned n,g;
    std::vector<std::vector<unsigned>> routes;
    std::vector<double> bandwidth,alpha;
    std::vector<uint64_t> loads;
};
struct Decision {
    std::vector<int> matching;
    unsigned threshold=1,seed_weight=0,weight=0,probes=0;
};
struct Stats {
    uint64_t stages=0,decomposition_runs=0,threshold_probes=0,matching_calls=0;
    uint64_t cycle_trials=0,changed_matches=0,lower_coefficient_rejections=0;
    uint64_t infeasible_cycles=0,nonimproving_rejections=0;
    std::vector<Decision> decisions;
};
class Planner {
public:
    Planner(const native_topology::Topology&,std::vector<uint64_t>,const std::string&);
    Decision choose(const std::vector<unsigned>&,const hungarian_info_t&);
    void decompose_into(FastAll2All&);
    uint64_t remaining_inter_bytes()const;
    const Stats& stats()const{return counts;}
    double preprocess_us=0;
private:
    const native_topology::Topology& top;
    std::vector<uint64_t> remaining;
    Options options;
    Stats counts;
    std::unique_ptr<ResourceCache> resources;
    void consume(const std::vector<int>&,unsigned);
};
}
