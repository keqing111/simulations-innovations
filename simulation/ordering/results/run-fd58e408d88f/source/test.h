#pragma once

// ============================================================================
// 实验驱动层：负责"造数据 -> 跑仿真 -> 对比基线 -> 输出结果"。
//
// 包含三个类：
//   zipf_distribution   —— 生成 Zipf 偏斜的流量（测试 skewness 用）；
//   DecompositionTester —— 只测 FastAll2All 分解本身的正确性与耗时；
//   FastAll2AllTester   —— 端到端 benchmark 驱动，main() 主要用它。
//
// 所有 benchmark 的套路基本一致：
//   1. 随机（或 Zipf）生成一个 dim × dim 的流量矩阵，对角线清零；
//   2. 每个源 server 建一个 LocalScheduler，再建 GlobalScheduler；
//   3. 跑 pipeline/pipeline2/pipeline3 得到 FAST 的模型时间；
//   4. 用 spread_out_baseline 估基线时间，并计算理论最优带宽；
//   5. 多次重复取均值/误差，写入 benchmark/ 下的文本文件。
//      （例外：transfer_size 只采样一次；topology 和 run() 只打印，不写文件。）
// ============================================================================

#include <iostream>
#include <vector>
#include <ctime>
#include <cstdlib>
#include "config.h"
#include "all2all.h"

using namespace std;


// 随机负载的单个矩阵元素上限（实际取值 0..MAX_ELEMENT-1）。矩阵元素默认按
// "1 个单位"计，transfer_size_benchmark 会通过 MBpu 把单位换算成实际 MB。
#define MAX_ELEMENT 10000


//-----------------------------------------------------------------------------------------------------------------------
// Zipf 分布采样器：用来生成"少数大流、大量小流"的偏斜负载。
// 通过数值方法求逆 CDF（zipf_inverse_cdf_fast），再对 [0,1] 均匀采样即可
// 得到服从 Zipf(s) 的流量大小。s 越大越偏斜。
// Modified from: https://github.com/nal-epfl/castan/blob/master/scripts/pcap_tools/create_zipfian_distribution_pcap.py
//-----------------------------------------------------------------------------------------------------------------------
class zipf_distribution{
private:
    double s;   // 偏斜参数，0 < s < 1（越大越不均）
public:
    zipf_distribution(double _s): s(_s){srand(time(NULL));}
    ~zipf_distribution(){}
    uint32_t zipf_inverse_cdf_fast(double p, uint32_t N);
    void zipf(vector<uint> * r, uint N);
};


// 只测 FastAll2All 分解：对若干维度随机生成矩阵，分别计时
// 建矩阵 / scaling / 双随机化 / 分解，并用 verify_decomposition 校验正确性。
class DecompositionTester{
private:
    uint times_per_dim;
    vector<uint> test_dims;

public:
    DecompositionTester(vector<uint> _test_dims, uint _times_per_dim = 10): test_dims(_test_dims), times_per_dim(_times_per_dim){ srand((unsigned)time(0));}
    ~DecompositionTester(){}
    void run(bool enable_scaling=false);
};

// 端到端 benchmark 驱动。构造参数决定默认规模、重复次数、是否开启 scaling、
// 以及默认的 server 间/内链路类型；各个 benchmark 方法可以在调用时覆盖这些默认值。
class FastAll2AllTester{
private:
    uint test_times;                 // 每个配置点重复多少次（用于算均值/误差）
    bool enable_scaling;             // 是否在分解前做 scaling
    uint server_n;
    uint gpu_n;
    INTER_LINK_TYPE inter_link_type;
    INTRA_LINK_TYPE intra_link_type;

public:
    FastAll2AllTester(uint s_n, uint g_n, uint times = 10, bool scaling=false, INTER_LINK_TYPE inter_type=INFB, INTRA_LINK_TYPE intra_type=DGX2);
    ~FastAll2AllTester(){}
    void run();                                                         // 单配置点：FAST vs Spread-out
    void server_gpu_number_benchmark(INTRA_LINK_TYPE _intra_type = DGX2, INTER_LINK_TYPE _inter_type = INFB); // 扫描规模
    void fabric_speed_benchmark();                                      // 扫描 server 内带宽
    void topology_benchmark(intra_transfer_topo_fn fn, uint inter_Gbps, uint intra_Gbps); // 指定拓扑模型
    void skewness_benchmark(INTRA_LINK_TYPE _intra_type=DGX2, INTER_LINK_TYPE _inter_type=INFB); // 扫描 Zipf 偏斜
    void transfer_size_benchmark(bool enable_scaling, INTRA_LINK_TYPE _intra_type=DGX2, INTER_LINK_TYPE _inter_type=INFB, scaling_success_condition fn = &balance_alpha_beta, string fstr = "0"); // 扫描消息大小
};

// 下面几个结构体是 benchmark 结果 map 的 key（配置点），
// operator< 让它们能按各自的配置字段（server 数优先）排序后输出。
struct server_gpu_config_t{
    uint svr_n;
    uint gpu_n;
    bool operator<(const server_gpu_config_t &o) const {
    return svr_n < o.svr_n || (svr_n == o.svr_n && gpu_n < o.gpu_n);
}
};

struct server_speed_config_t{
    uint svr_n;
    uint speed;    // intra 链路 Gbps
    double ratio;  // intra / inter 带宽比
    bool operator<(const server_speed_config_t &o) const {
    return svr_n < o.svr_n || (svr_n == o.svr_n && speed < o.speed);
    }
};

struct server_skewness_config_t{
    uint svr_n;
    double s;      // Zipf 偏斜参数
    bool operator<(const server_skewness_config_t &o) const {
    return svr_n < o.svr_n || (svr_n == o.svr_n && s < o.s);
}
};

struct server_transfer_config_t{
    uint svr_n;
    double sz;     // 消息大小上限（MB）：矩阵中最大元素对应的规模
    bool operator<(const server_transfer_config_t &o) const {
    return svr_n < o.svr_n || (svr_n == o.svr_n && sz < o.sz);
}
};