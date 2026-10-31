#pragma once

// ============================================================================
// LocalScheduler：站在"一个源 server"的视角，负责 server 内部的调度。
//
// 数据结构（只保存本 server 的 GPU 行）：
//   data[gpu_i][dst_global_gpu]  原始需求，dst_global_gpu = dst_server*gpu_n + dst_gpu
//   balanced_data[gpu_i][...]    当前剩余待发数据：初始是 data 的拷贝，
//                                balance/dispatch 会原地修改它
//   row_sum[server*gpu_n + i]    本 server 的 GPU i 发往 server 的剩余总量
//   server2server_data[server]   本 server 每个 GPU 平均要发给该 server 的量
//   intrinsic_all2all[gpu_i*gpu_n + gpu_j]
//                                同一 server 内 GPU i -> GPU j 的流量（不需要过网络）
//
// 它主要做三件事：
//   1. prepare_load_balance()：统计每个目的 server 的每 GPU 平均发送量，
//      并抽出 server 内部的 all-to-all 流量（intrinsic_all2all）；
//   2. balance：在源 server 的 GPU 之间搬数据，让每个 GPU 发往某目的 server
//      的总量一致（避免某个 GPU 成为瓶颈）；
//   3. dispatch：每个阶段只给每块 GPU 分配 freq 的发送量，并记录为此需要
//      在 server 内做的 GPU->GPU 数据搬运（balance/dispatch 矩阵）。
//
// balance_result 里的两个矩阵都是 gpu_n × gpu_n：
//   balance[i][j]  = 为均衡负载，从源 GPU i 搬到源 GPU j 的数据量；
//   dispatch[i][j] = 为对齐发送 GPU，从源 GPU i 搬到源 GPU j（再发往远端 j）的量。
// 这两个矩阵就是流水线计时模型里的 server 内开销来源。
// ============================================================================

#include <iostream>
#include "config.h"
#include "define.h"
#include <map>
#include <cstdint>

using namespace std;


struct load_balance_result{
    uint * balance;   // gpu_n*gpu_n，负荷均衡阶段的内部搬移量
    uint * dispatch;  // gpu_n*gpu_n，发送对齐阶段的内部搬移量
};

class LocalScheduler{
private:
    uint ** data;                 // 本 server 各 GPU 的原始发送需求
    uint ** balanced_data;        // 平衡后、尚未被调度消耗的需求（会被原地修改）
    uint * intrinsic_all2all;     // 本 server 内部的 all-to-all 流量（同 server 内 GPU 间）
    uint gpu_n;
    uint server_n;
    uint server_id;
    enum INTRA_LINK_TYPE intra_type;
    struct link_info_t intra_info;   // 本 server 的 server 内链路模型
    map<uint ,struct load_balance_result> intra_all2all; // 目的 server -> 服务器内操作（仅调试路径尝试使用；正常 benchmark 不填充）
    uint * row_sum;               // [dst_server*gpu_n + gpu] 发往该 server 的剩余量

public:
    uint * server2server_data;    // [dst_server] 每块 GPU 的发送目标量：平衡后的平均值；approx 模式下是最大行和
    // _data 指向本 server 第一行在全局矩阵中的位置（行优先，每行 server_n*gpu_n 个元素）
    LocalScheduler(uint* _data, uint _gpu_n, uint _server_n, uint _server_id, INTRA_LINK_TYPE _type = DGX2);
    LocalScheduler(uint* _data, uint _gpu_n, uint _server_n, uint _server_id, struct link_info_t _intra_link);
    ~LocalScheduler();
    void load_balance();          // 演示/调试：逐步打印 balance 和 dispatch 过程
    // 比较"先平衡再派发"与"直接派发"的代价，决定是否跳过平衡（返回 true 表示可跳过）
    bool check_balance_dispatch(uint to_server_id, double MBpu = 1.0);
    // 跳过平衡时的近似：直接把每个 GPU 的最大行和作为目标量
    void server2server_approx(uint to_server_id);
    // 按 server2server_data 的目标量做一次性平衡（pipeline2/pipeline3 使用）
    void server2server_balance(uint to_server_id, struct load_balance_result r);
    // 计算 row_sum、intrinsic_all2all 以及每个目的 server 的每 GPU 目标发送量
    void prepare_load_balance();
    // 平衡到指定频率 freq，然后立刻提取本阶段的 dispatch（pipeline 的逐阶段方案）
    void server2server_balance(uint to_server_id, struct load_balance_result r, uint freq);
    // 不做平衡，直接从当前数据中提取 freq 的发送量并记录 dispatch（pipeline2 使用）
    void server2server_dispatch(uint to_server_id, struct load_balance_result r, uint freq);
    uint get_server_id(){return server_id;}
    struct link_info_t get_link_info(){return intra_info;}
    uint * get_intrinsic_all2all(){return intrinsic_all2all;}
    // 某 GPU 当前还欠某目的 server 多少数据（尚未 dispatch 的部分；
    // 若已调用过 balance，则是平衡后的剩余）
    uint64_t pending_units(uint to_server_id, uint gpu_id) const;
    void print();
    void print(uint dst_server_id);
    void print_load_balance_step(struct load_balance_result *r);
};
