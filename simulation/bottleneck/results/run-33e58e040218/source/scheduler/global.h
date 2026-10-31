#pragma once
#include <iostream>
#include <vector>
#include "matrix.h"
#include "local.h"
#include "all2all.h"
#include "config.h"
#include <functional>
#include <cstdint>

using namespace std;

struct pipeline_result_t{
    double t;
    double ratio;
};

// Optional native-study hooks. Default pipeline2 callers retain legacy costing.
// Units remain native matrix units; bytes = units * MBpu * 1e6.
struct pipeline_hooks_t {
    std::function<void(Matrix&, const vector<uint64_t>&, FastAll2All&)> prepare;
    std::function<double(const PermutationSet&, const vector<uint64_t>&)> communication;
    double balance_us = 0, intrinsic_us = 0, native_decompose_us = 0;
    vector<double> dispatch_us;
    vector<PermutationSet> stages;
    vector<vector<uint64_t>> payload_units;
};

class GlobalScheduler{
private:
    uint server_n;
    uint gpu_n;
    bool enable_scaling;
    Matrix mat;
    vector<LocalScheduler*> locals; 
    enum INTER_LINK_TYPE inter_type;
    struct link_info_t inter_info;
    enum INTRA_LINK_TYPE intra_type;
    struct link_info_t intra_info;
    bool* if_approx;
public:
    GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, INTER_LINK_TYPE type = INFB, INTRA_LINK_TYPE itype = DGX2, bool scaling = false, double MBpu = 1.0);
    GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, struct link_info_t _inter_link, struct link_info_t _intra_link, bool scaling = false, double MBpu = 1.0);
    ~GlobalScheduler();
    struct link_info_t get_link_info(){return inter_info;}
    struct pipeline_result_t pipeline(double MBpu = 1.0);
    struct pipeline_result_t pipeline2(double MBpu = 1.0, scaling_success_condition fn = &balance_alpha_beta, pipeline_hooks_t* hooks = nullptr);
    struct pipeline_result_t pipeline3(double MBpu = 1.0, intra_transfer_topo_fn fn= &intra_transfer_full_mesh);
    double permutation_set_cost(uint freq, double MBpu = 1.0);
};
