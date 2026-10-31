#include "config.h"
#include "define.h"
#include <iomanip>
#include <cmath>

// ============================================================================
// 链路参数与代价函数的实现。
//
// 所有时间单位都是微秒（us），所有流量最终都换算成 MB：
//     time = alpha + size_MB * beta
// 其中 alpha 表示一次通信的固定开销，beta 表示每 MB 的传输开销。
// ============================================================================

using namespace std;

// server 内链路参数。beta 越小表示单 GPU 的 server 内带宽越高。
// 例如 H100：beta=2.3 us/MB ≈ 435 GB/s；DGX2 的 beta=8 us/MB ≈ 125 GB/s。
// 这些数值是为仿真设定的近似值，注释中的 TODO 表示还需要用实测数据校准。
struct link_info_t get_intra_link_info(INTRA_LINK_TYPE type){
    struct link_info_t r;
    switch (type){
        case DGX2:
            r.alpha = 0.1;
            r.beta = 8; //TODO: check specs and measure
            break;
        case FAST:
            r.alpha = 0.1;
            r.beta = 1; //TODO: check specs and measure
            break;
        case B100:
            r.alpha = 0.1;
            r.beta = 1.2; //TODO: check specs and measure
            break;
        case H100:
            r.alpha = 0.1;
            r.beta = 2.3; //TODO: check specs and measure
            break;
        case NDV2:
            r.alpha = 0.1;
            r.beta = 46;
            break;
        case MI300X:
            //TODO: check MI300X specs
            r.alpha = 0.1;
            r.beta = 5;
            break;
    }

    return r;
}

// server 间链路参数。可以理解为每块 GPU 用于跨 server 发送的网卡能力：
// INFB 的 beta=106 us/MB ≈ 9.4 GB/s；ETHER400 的 beta=22 us/MB ≈ 45 GB/s。
struct link_info_t get_inter_link_info(INTER_LINK_TYPE type){
    struct link_info_t r;
    switch (type){
        case INFB:
            r.alpha = 1.7;
            r.beta = 106;
            break;
        case ETHER100:
            //TODO: measure ethernet specs
            r.alpha = 0.7;
            r.beta = 84;
            break;
        case ETHER400:
            r.alpha = 0.7;
            r.beta = 22;
            break;
    }
    return r;
}

// Server 内轮转式 all-to-all 的代价：按对角线轮转，dim 步完成。
// 第 i 步里，每个源 GPU j 把数据发给 (j+i)%dim；所有 GPU 同时发，
// 因此这一步的耗时由本步最大的那个元素决定（alpha + max*beta）。
// 总时间 = 各步耗时之和。这个模型对应没有全互联、只能分步转发的拓扑。
double spread_out(uint * data, uint dim, struct link_info_t link, double MBpu){
    double r = 0.0;
    uint largest_in_diagnal = 0;
    for (uint i = 0; i < dim; i ++){
        largest_in_diagnal = 0;
        for (uint j = 0; j < dim; j++){
            // 第 i 条"对角线"：行 j -> 列 (j+i) mod dim
            largest_in_diagnal = MAX(largest_in_diagnal, data[j * dim + ((j + i) % dim)]);
        }
        if (largest_in_diagnal != 0){
            r += link.alpha +  largest_in_diagnal * link.beta * MBpu;
        }
    }
    return r;
}

// 全互联（full mesh / NVSwitch 类）拓扑：所有 GPU 两两之间可以同时传，
// 整个 all-to-all 一步完成，耗时只取决于最大的那条流量。
double intra_transfer_full_mesh(uint * data, uint dim, struct link_info_t link, double MBpu){
    uint max_ele = 0;
    for (uint i = 0; i < dim; i ++){
        for (uint j = 0; j < dim; j++){
            max_ele = MAX(max_ele, data[i * dim + j]);
        }
    }
    return link.alpha +  max_ele * link.beta * MBpu;
}

// 8 GPU 混合立方网格（DGX-1 时代的 hybrid cube-mesh）的专门模型。
// 这个函数把 8 个 GPU 的拓扑和每一步的路径都写死了，只适用于 dim=8。
//
// 拓扑示意：
//      0    1     4    5
//      2    3     6    7
// 左、右各是一个 2×2 小网格；从 r/t 的索引可以看出，跨组直连对是
// 0-5、1-4、2-7、3-6，其余跨组流量在 t[8] 中按经中间 GPU 转发计算。
//
// 代价分两步算：
//   r[32]：32 条有直连链路的有序 GPU 对（组内小网格 + 跨组直连）的传输时间，
//           取最慢的一条；
//   t[8] ：缺少直连、需要两跳的 24 条跨组流量按目的聚合成 8 项。每一项大致为
//           2 次 alpha + 第一跳的最大流量 × beta + 第二跳共享流量 × (beta 或
//           beta/2，按该路径硬编码标定)；同样取最慢的一条。两步相加就是总耗时。
// 数组下标含义（r/t 中的第 k 项）在每行右侧有注释，改动前请先画拓扑图。
double intra_transfer_hybrid_cude_mesh(uint * data, uint dim, struct link_info_t link, double MBpu){
//50 GBps bidirectional -> 25 GBps duplex
//      0    1     4    5
//      2    3     6    7
    // first do transfer at small ring
    double result = 0;
    double r[32];
    r[0] = link.alpha +   data[0 * dim + 1] * link.beta / 2 * MBpu;
    r[1] = link.alpha +   data[0 * dim + 2] * link.beta / 2 * MBpu;
    r[2] = link.alpha +   data[0 * dim + 3] * link.beta * MBpu;
    r[3] = link.alpha +   data[1 * dim + 0] * link.beta / 2 * MBpu;
    r[4] = link.alpha +   data[1 * dim + 2] * link.beta * MBpu;
    r[5] = link.alpha +   data[1 * dim + 3] * link.beta * MBpu;
    r[6] = link.alpha +   data[2 * dim + 0] * link.beta / 2 * MBpu;
    r[7] = link.alpha +   data[2 * dim + 1] * link.beta * MBpu;
    r[8] = link.alpha +   data[2 * dim + 3] * link.beta / 2 * MBpu;
    r[9] = link.alpha +   data[3 * dim + 0] * link.beta * MBpu;
    r[10] = link.alpha +  data[3 * dim + 1] * link.beta * MBpu;
    r[11] = link.alpha +   data[3 * dim + 2] * link.beta / 2 * MBpu;
    r[12] = link.alpha +   data[0 * dim + 5] * link.beta * MBpu;
    r[13] = link.alpha +   data[1 * dim + 4] * link.beta / 2 * MBpu;
    r[14] = link.alpha +   data[2 * dim + 7] * link.beta * MBpu;
    r[15] = link.alpha +   data[3 * dim + 6] * link.beta / 2 * MBpu;
    r[16] = link.alpha +   data[5 * dim + 0] * link.beta * MBpu;
    r[17] = link.alpha +   data[4 * dim + 1] * link.beta / 2 * MBpu;
    r[18] = link.alpha +   data[7 * dim + 2] * link.beta * MBpu;
    r[19] = link.alpha +   data[6 * dim + 3] * link.beta / 2 * MBpu;
    r[20] = link.alpha +   data[4 * dim + 5] * link.beta / 2 * MBpu;
    r[21] = link.alpha +   data[4 * dim + 6] * link.beta * MBpu;
    r[22] = link.alpha +   data[4 * dim + 7] * link.beta * MBpu;
    r[23] = link.alpha +   data[5 * dim + 4] * link.beta / 2 * MBpu;
    r[24] = link.alpha +   data[5 * dim + 6] * link.beta * MBpu;
    r[25] = link.alpha +   data[5 * dim + 7] * link.beta / 2 * MBpu;
    r[26] = link.alpha +   data[6 * dim + 4] * link.beta * MBpu;
    r[27] = link.alpha +   data[6 * dim + 5] * link.beta * MBpu;
    r[28] = link.alpha +   data[6 * dim + 7] * link.beta / 2 * MBpu;
    r[29] = link.alpha +   data[7 * dim + 4] * link.beta * MBpu;
    r[30] = link.alpha +  data[7 * dim + 5] * link.beta / 2 * MBpu;
    r[31] = link.alpha +   data[7 * dim + 6] * link.beta / 2 * MBpu;

    // 第一跳：32 条可能的直连/小网格传输并行，耗时取最慢的一条
    double first_step_max = 0;
    for (uint i = 0; i < 32; i ++){
        first_step_max = MAX(first_step_max, r[i]);
    }
    result += first_step_max;
    // transfer via intermediate hop
    // 第二跳：需要跨组、经中间 GPU 转发的 8 组流量（两跳延迟 + 两段带宽）
    double t[8];
    //      0    1     4    5 
    //      2    3     6    7
    t[0] = 2 * link.alpha +  MAX(MAX(data[0 * dim + 4] / 2, data[2 * dim + 4]), data[3 * dim + 4]) * link.beta * MBpu + (data[0 * dim + 4] + data[2 * dim + 4] + data[3 * dim + 4]) * link.beta / 2 * MBpu;
    t[1] = 2 * link.alpha +  MAX(MAX(data[1 * dim + 5] / 2, data[2 * dim + 5] / 2), data[3 * dim + 5]) * link.beta * MBpu + (data[1 * dim + 5] + data[2 * dim + 5] + data[3 * dim + 5]) * link.beta * MBpu;
    t[2] = 2 * link.alpha +  MAX(MAX(data[0 * dim + 6], data[1 * dim + 6]), data[2 * dim + 6] / 2) * link.beta * MBpu + (data[0 * dim + 6] + data[1 * dim + 6] + data[2 * dim + 6]) * link.beta / 2 * MBpu;
    t[3] = 2 * link.alpha +  MAX(MAX(data[0 * dim + 7] / 2, data[1 * dim + 7]), data[3 * dim + 7] / 2) * link.beta * MBpu + (data[0 * dim + 7] + data[1 * dim + 7] + data[3 * dim + 7]) * link.beta * MBpu;
    t[4] = 2 * link.alpha +  MAX(MAX(data[4 * dim + 0] / 2, data[6 * dim + 0]), data[7 * dim + 0]/2) * link.beta * MBpu + (data[4 * dim + 0] + data[6 * dim + 0] + data[7 * dim + 0]) * link.beta * MBpu;
    t[5] = 2 * link.alpha +  MAX(MAX(data[5 * dim + 1] / 2, data[6 * dim + 1]), data[7 * dim + 1]) * link.beta * MBpu + (data[5 * dim + 1] + data[6 * dim + 1] + data[7 * dim + 1]) * link.beta / 2 * MBpu;
    t[6] = 2 * link.alpha +  MAX(MAX(data[4 * dim + 2], data[5 * dim + 2] / 2), data[6 * dim + 2] /2) * link.beta * MBpu + (data[4 * dim + 2] + data[5 * dim + 2] + data[6 * dim + 2]) * link.beta * MBpu;
    t[7] = 2 * link.alpha +  MAX(MAX(data[4 * dim + 3], data[5 * dim + 3]), data[7 * dim + 3] /2) * link.beta * MBpu + (data[4 * dim + 3] + data[5 * dim + 3] + data[7 * dim + 3]) * link.beta / 2 * MBpu;
    // 第二步同样由最慢的一条路径决定
    double second_step_max = 0;
    for (uint i = 0; i < 8; i ++ ){
        second_step_max = MAX(second_step_max, t[i]);
    }
    result += second_step_max;
    return result;
}


// 双环（2 ring）拓扑：GPU 排成两个方向相反的环，每个方向一次只能走一步。
//   - 正向环：第 i 步完成"错开 i 格"的对角线传输，耗时按
//     (alpha + 该对角线最大值 × beta) × i 计——数据要经过 i 跳才到；
//   - 反向环同理。
// 两个环可以同时工作，总时间由较慢的一侧决定（取 MAX）。
// 注意：这里的 i 同时作为"步数"乘在时间上，是对环上逐跳转发的简化建模。
double intra_transfer_2ring(uint * data, uint dim, struct link_info_t link, double MBpu){
//100 GBps bidirectional -> 50 GBps duplex
// 2 rings
double first_ring = 0;
for (uint i = 1; i < 4; i++){
    uint largest_in_diagnal = 0;
    for (uint j = 0; j < dim; j++){
        largest_in_diagnal = MAX(largest_in_diagnal, data[j * dim + ((j + i) % dim)]);
    }

    if (largest_in_diagnal != 0){
        first_ring += (link.alpha +  largest_in_diagnal * link.beta * MBpu) * i;
    }
}
double reverse_ring = 0;
for (uint i = 1; i < 5; i++){
    uint largest_in_diagnal = 0;
    for (uint j = 0; j < dim; j++){
        largest_in_diagnal = MAX(largest_in_diagnal, data[j * dim + ((j + dim - i) % dim)]);
    }

    if (largest_in_diagnal != 0){
        reverse_ring += (link.alpha +  largest_in_diagnal * link.beta * MBpu) * i;
    }
}

return MAX(first_ring, reverse_ring);
}


// 经典 Spread-out all-to-all 基线的耗时模型（用于和 FAST 对比）。
//
// 整个系统共有 dim = server_n * gpu_n 个 GPU，矩阵也是 dim × dim。
// Spread-out 的执行方式：
//   做 dim 个轮次；第 i 轮里，每个 GPU j 把自己矩阵中第 (j+i)%dim 列的数据
//   发给对应 GPU。所有 GPU 同时发，因此这一轮的时间由"最慢的那个发送"决定
//   （同一 server 内走 intra 链路，跨 server 走 inter 链路）。
// 把每一轮的最慢时间累加就是总时间。
// 这就是论文里 n*m-1 轮跨 server 轮转的基线（每轮仍可能夹带 server 内发送）。
double spread_out_baseline(uint * data, uint server_n, uint gpu_n, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu){
    double r = 0.0;
    uint dim = server_n * gpu_n;
    double current_sending = 0.0, largest_sending = 0.0;
    double alpha = 0, beta = 0;   // 仅用于调试统计
    for (uint i = 0; i < dim; i++){
        largest_sending = 0;
        for (uint j = 0; j < dim; j++){
            uint row_id = j, col_id = (j + i) % dim;   // 第 i 轮：行 j 发给列 (j+i)%dim
            if (data[row_id * dim + col_id] != 0){
                if (row_id / gpu_n == col_id / gpu_n){
                    // intra server links：同一 server 内用本地链路
                    current_sending = intra_link.alpha +  data[row_id * dim + col_id] * intra_link.beta * MBpu;
                }else{
                    // inter server links：跨 server 用网络链路
                    current_sending = inter_link.alpha +  data[row_id * dim + col_id] * inter_link.beta * MBpu;
                }
                largest_sending = MAX(largest_sending, current_sending);
            }
        }
        alpha += inter_link.alpha;
        beta += largest_sending - inter_link.alpha;
        r += largest_sending;   // 本轮所有并行发送完成需要的最长时间
    }
    // cout << "alpha: " << alpha << ", beta: " << beta <<endl;
    return r;
}


void print_matrix(uint * data, uint m, uint n){ //width m, height n
    cout << "--------------------------------------"<<endl;
    for(uint i = 0; i < n; i ++){
        for (uint j = 0; j < m; j++){
            cout << setw(10);
            cout << data[i * m + j];
        }
        cout << endl;
    }
    cout << "--------------------------------------"<<endl;
}

// 计算样本均值和总体标准差（除以 n，而不是 n-1）。
void compute_average_standard_deviation(vector<double> *vec, double * avg, double * sd){
    double sum = 0.0, mean = 0.0, standard_deviation = 0.0;
    uint sz = vec->size();
    for(uint i = 0; i < sz; i++){
        sum += (*vec)[i];
    }
    mean = sum / (double)sz;
    for (uint i = 0; i < sz; i++){
        standard_deviation += pow((*vec)[i] - mean, 2);
    }
    (*avg) = mean;
    (*sd) = sqrt(standard_deviation / sz);
}

// 把"每秒多少 Gb"换算成"每 MB 多少微秒"。
// 推导：1 MB = 8e6 bit，speed Gbps = speed*1e9 bit/s，
//   传输 1 MB 需要 8e6/(speed*1e9) 秒 = 8e3/speed 微秒。
// 所以结果是 8 * 1e3 / speed（speed 单位为 Gbps）。
double Gbps_to_us_per_MB(uint speed){
    return (double) (8 * 1e3) / (double) speed;
}
