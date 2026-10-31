#pragma once

// ============================================================================
// 链路模型与基础代价函数。
//
// 仿真器不模拟逐包行为，而是用经典的 alpha-beta 线性模型估算一次传输：
//     传输时间 = alpha + 数据量(MB) × beta
//   alpha：每次通信的固定延迟（us），与数据量无关；
//   beta ：每 MB 数据的串行化时间（us/MB），本质是带宽的倒数。
//
// 这个文件提供：
//   - 各类 server 内 / server 间链路的 alpha、beta 参数；
//   - 几种 server 内 all-to-all 拓扑的代价模型（spread_out、full mesh、双环等）；
//   - 基线算法 Spread-out 的端到端耗时模型；
//   - benchmark 用的平均值/标准差、带宽单位换算等小工具。
// ============================================================================

#include <iostream>
#include <vector>
using namespace std;


// server 内互连类型（每块 GPU 的网卡/NVLink 能力）。
enum INTRA_LINK_TYPE{
    DGX2, MI300X, NDV2, FAST, H100, B100
};

// server 间网络类型。
enum INTER_LINK_TYPE{
    ETHER100, ETHER400, INFB
};

struct link_info_t{
    double tput;    // 预留字段，当前模型没有使用
    double alpha;   // 每消息固定延迟，单位 us
    double beta;    // 每 MB 传输时间，单位 us/MB（串行化时间，与带宽成反比）
};


// 根据类型返回硬编码的链路参数。真实参数需要实测校准（代码里有 TODO）。
struct link_info_t get_intra_link_info(INTRA_LINK_TYPE tyoe);
struct link_info_t get_inter_link_info(INTER_LINK_TYPE type);

// Spread-out（经典 all-to-all 基线）的耗时模型：
// 每个 GPU 分 dim 步轮流转发，每步受最慢的那条链路限制。
double spread_out_baseline(uint * data, uint server_n, uint gpu_n, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu = 1.0);


// server 内 all-to-all 的代价函数类型：输入是一个 gpu_n × gpu_n 的
// "本 server 各 GPU 之间要交换的流量矩阵"（行优先展开），返回预计耗时。
// 不同的网络拓扑用不同实现，供 pipeline3/topology_benchmark 替换。
typedef double (*intra_transfer_topo_fn)(uint *, uint, struct link_info_t, double);
// 按对角线轮转的 server 内 all-to-all 代价模型：dim 步，每步所有 GPU 同时
// 发给错开 i 个位置的 GPU，取最慢者累加。
double spread_out(uint * data, uint dim, struct link_info_t link, double MBpu = 1.0);
// 全互联（如 NVSwitch）：所有数据一步并行换完，时间由最大元素决定。
double intra_transfer_full_mesh(uint * data,  uint dim, struct link_info_t link, double MBpu = 1.0);
// 8 GPU 混合立方网格（hybrid cube-mesh）的专门模型：先小环交换，再经中间跳转发。
double intra_transfer_hybrid_cude_mesh(uint * data,  uint dim, struct link_info_t link, double MBpu = 1.0);
// 双环拓扑：正向环 + 反向环，各自按步累加，取较慢的一侧。
double intra_transfer_2ring(uint * data,  uint dim, struct link_info_t link, double MBpu = 1.0);



// 调试用：按 width × height 打印行优先矩阵。
void print_matrix(uint * data, uint m, uint n);

// benchmark 统计结果：均值与误差（误差取标准差 / sqrt(采样次数)）。
struct statistics_t{
    double avg;
    double err;
};

// 每个配置点上的三种算法带宽，单位 GB/s（见 test.cpp 的换算）。
struct simulation_result_t{
    double flash_algbw;   // FAST 的算法带宽
    double opt_algbw;     // 理论最优上界
    double spread_algbw;  // Spread-out 基线的算法带宽
};

void compute_average_standard_deviation(vector<double> *vec, double * avg, double * sd);
// Gbps -> us/MB：1 MB = 8 Mb，所以 8*1e3 / speed 微秒。
double Gbps_to_us_per_MB(uint speed);