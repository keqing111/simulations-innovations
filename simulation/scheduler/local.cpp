#include <cstring>
#include "local.h"
#include <vector>
#include <iomanip>
#include <stdio.h>

// ============================================================================
// LocalScheduler 的实现。
//
// 关键索引约定（很容易看晕，先记住）：
//   - 全局矩阵的每个元素是 (源 GPU) -> (目的 GPU) 的流量；
//   - 本类只取"源 server 为本 server"的那 gpu_n 行；
//   - row_sum[to_server * gpu_n + i] 表示本 server 的 GPU i 发往
//     to_server 的总量；
//   - data[i][to_server*gpu_n + j] 表示本 server 的 GPU i 发往
//     to_server 的 GPU j 的量。
//
// 调度分两个动作：
//   balance  —— 在本 server 的 GPU 之间搬数据，使每个 GPU 对某目的 server
//               的待发总量相同（否则某块 GPU 会先发完、其他 GPU 空等）；
//   dispatch —— 每个流水阶段只让每块 GPU 发 freq 的量，并把"本 GPU 要发的
//               远端 GPU j 的数据"在本 server 内对齐到 GPU j。
// 两个动作都会产生 server 内的 GPU->GPU 流量，模型会把它们算进总时间。
// ============================================================================

// 构造函数：_data 指向本 server 在全局矩阵中的第一行。
// 全局矩阵行优先、每行 server_n*gpu_n 个元素，所以这里连续读 gpu_n 行、
// 每行 dim 列，就得到"本 server 所有源 GPU -> 所有目的 GPU"的完整需求。
LocalScheduler::LocalScheduler(uint* _data, uint _gpu_n, uint _server_n, uint _server_id, INTRA_LINK_TYPE _type){
    gpu_n = _gpu_n;
    server_n = _server_n;
    server_id = _server_id;
    uint dim = gpu_n * server_n;
    // print_matrix(_data, dim, gpu_n);
    data = new uint*[gpu_n];
    balanced_data = new uint*[gpu_n];
    uint idx = 0;
    for (uint i = 0; i < gpu_n; i++){
        data[i] = new uint[dim];
        balanced_data[i] = new uint[dim];
        for (uint j = 0; j < dim; j++){
            data[i][j] = _data[idx];
            balanced_data[i][j] = _data[idx];
            idx++;
        }
    }
    intra_type = _type;
    intra_info = get_intra_link_info(intra_type);
    server2server_data = new uint[server_n];
    memset(server2server_data, 0, server_n * sizeof(uint));
    row_sum = new uint[gpu_n * server_n];
    memset(row_sum, 0, gpu_n * server_n * sizeof(uint));
    intrinsic_all2all = new uint[gpu_n * gpu_n];
    memset(intrinsic_all2all, 0, gpu_n * gpu_n * sizeof(uint));
}

// 与上一个构造函数相同，只是直接接收一份自定义的链路参数
// （topology_benchmark/fabric_speed_benchmark 会用它注入不同带宽）。
LocalScheduler::LocalScheduler(uint* _data, uint _gpu_n, uint _server_n, uint _server_id, struct link_info_t _intra_link){
    gpu_n = _gpu_n;
    server_n = _server_n;
    server_id = _server_id;
    uint dim = gpu_n * server_n;
    // print_matrix(_data, dim, gpu_n);
    data = new uint*[gpu_n];
    balanced_data = new uint*[gpu_n];
    uint idx = 0;
    for (uint i = 0; i < gpu_n; i++){
        data[i] = new uint[dim];
        balanced_data[i] = new uint[dim];
        for (uint j = 0; j < dim; j++){
            data[i][j] = _data[idx];
            balanced_data[i][j] = _data[idx];
            idx++;
        }
    }
    intra_info = _intra_link;
    server2server_data = new uint[server_n];
    memset(server2server_data, 0, server_n * sizeof(uint));
    row_sum = new uint[gpu_n * server_n];
    memset(row_sum, 0, gpu_n * server_n * sizeof(uint));
    intrinsic_all2all = new uint[gpu_n * gpu_n];
    memset(intrinsic_all2all, 0, gpu_n * gpu_n * sizeof(uint));
}

LocalScheduler::~LocalScheduler(){
    for (uint i = 0; i < gpu_n; i++){
        delete[] data[i];
    }
    delete[] data;
    delete[] balanced_data;
    delete[] server2server_data;
    for (auto it = intra_all2all.begin(); it !=intra_all2all.end(); it++){
        struct load_balance_result r = it->second;
        delete[] r.balance;
        delete[] r.dispatch;
    }
    delete[] row_sum;
    delete[] intrinsic_all2all;
}

// 调试入口：手工演示"对每个目的 server 做一次 balance，再对 server 1 做几次 dispatch"
// 的过程并打印中间矩阵。注意里面的目的 server（1）和频率（6,5,4）是硬编码的，
// 只适合小规模手工观察；benchmark 走的是 GlobalScheduler 的 pipeline 系列。
void LocalScheduler::load_balance(){
    for (auto it = intra_all2all.begin(); it !=intra_all2all.end(); it++){
        struct load_balance_result r = it->second;
        delete[] r.balance;
        delete[] r.dispatch;
    }

    struct load_balance_result r;
    //initialize data space for intra-all2all
    r.balance = new uint[gpu_n * gpu_n];
    r.dispatch = new uint[gpu_n * gpu_n];
    memset(r.balance, 0, gpu_n * gpu_n * sizeof(uint));
    memset(r.dispatch, 0, gpu_n * gpu_n * sizeof(uint));
    prepare_load_balance();

    print();
    // for (uint i = 0; i < 3; i++){
    //     server2server_balance(1, r, 6 - i);
    //     print_load_balance_step(&r);
    //     cout << "--------------------------" << endl;
    //     print();
    //     memset(r.balance, 0, gpu_n * gpu_n * sizeof(uint));
    //     memset(r.dispatch, 0, gpu_n * gpu_n * sizeof(uint));
    // }

    for (uint i = 0; i < server_n; i++){
        if (i == server_id)
            continue;
        server2server_balance(i, r);
        print_load_balance_step(&r);
        cout << "--------------------------" << endl;
        print();
        memset(r.balance, 0, gpu_n * gpu_n * sizeof(uint));
        memset(r.dispatch, 0, gpu_n * gpu_n * sizeof(uint));
    }

    cout << "dispatch: " << endl;
    for (uint i = 0; i < 3; i++){
        server2server_dispatch(1, r, 6 - i);
        print_load_balance_step(&r);
        cout << "--------------------------" << endl;
        print();
        memset(r.balance, 0, gpu_n * gpu_n * sizeof(uint));
        memset(r.dispatch, 0, gpu_n * gpu_n * sizeof(uint));
    }



    // for (uint i = 0; i < server_n; i++){
    //     if (i == server_id){
    //         continue;
    //     }
    //     struct load_balance_result r;
    //     //initialize data space for intra-all2all
    //     r.balance = new uint[gpu_n * gpu_n];
    //     memset(r.balance, 0, gpu_n * gpu_n * sizeof(uint));
    //     r.dispatch = new uint[gpu_n * gpu_n];
    //     memset(r.dispatch, 0, gpu_n * gpu_n * sizeof(uint));
    //     server2server_balance(i, &r, &server2server_data[i]);
    //     intra_all2all.insert(make_pair(i, r));
    // }
    // server2server_data[server_id] = 0;

    delete[] r.balance;
    delete[] r.dispatch;
}

// 计算本 server 的调度元信息，必须在任何 balance/dispatch 之前调用：
//   1. 对每个目的 server，统计每块源 GPU 的待发总量 row_sum；
//   2. 对本 server（i == server_id）单独处理：这部分流量不需要过网络，
//      直接拷进 intrinsic_all2all，作为"server 内 all-to-all"的固有开销；
//   3. 对每个目的 server 计算每 GPU 的平均发送量（向上取整），
//      作为 server2server_data，也就是平衡阶段的目标值。
void LocalScheduler::prepare_load_balance(){
    memset(row_sum, 0, gpu_n * server_n * sizeof(uint));
    for (uint i = 0; i < server_n; i++){
        if (i == server_id){
            // 目的 server 就是自己：这些流量只在本 server 内换手，不经过 server 间网络
            server2server_data[i] = 0;
            for (uint j = 0; j < gpu_n; j++){
                for (uint k = 0; k < gpu_n; k++){
                    intrinsic_all2all[j * gpu_n + k] = data[j][server_id * gpu_n + k];
                }
            }
            continue;
        }
        for (uint j = 0; j < gpu_n; j++){
            // 源 GPU j 发往目的 server i 的总量（把该 server 的 gpu_n 列加起来）
            row_sum[i * gpu_n + j] = 0;
            for (uint k = 0; k < gpu_n; k++){
                row_sum[i * gpu_n + j] += data[j][i * gpu_n + k];
            }
        }

        // 每 GPU 平均要发多少（向上取整），作为后续 balance 的目标
        uint row_avg = 0;
        for (uint k = 0; k < gpu_n; k++){
            row_avg += row_sum[i * gpu_n + k];
        }
        server2server_data[i] = (row_avg + gpu_n - 1) / gpu_n;
    }
    // print();
    // for (uint i = 0; i < server_n; i++){
    //     cout << "to server " << i << ":" << server2server_data[i] << endl;
    // }
}

// 跳过平衡时的近似目标：直接把"最忙的那块 GPU 的发送量"当作每 GPU 目标。
// 这样相当于假设不搬数据也能接受不均衡，server2server_data 会偏大。
void LocalScheduler::server2server_approx(uint to_server_id){
    uint max_row = 0;
    for (uint k = 0; k < gpu_n; k++){
        max_row = MAX(row_sum[to_server_id * gpu_n + k], max_row);
    }
    server2server_data[to_server_id] = max_row;
}

// 启发式判断：对目的 server 而言，"先平衡再派发"是否值得。
//
// 估算两部分 server 内开销：
//   beta_cost  —— 搬数据的传输量：最忙 GPU 的行和（balance）+ 各轮 dispatch 的最大元素；
//   alpha_cost —— 固定延迟：每轮 dispatch 一次 alpha（外加 balance 的一次）。
// 如果 beta/alpha < 2，说明大部分时间花在固定延迟上（消息很小），
// 此时做 balance 不划算，返回 true 让上层跳过平衡、直接 dispatch。
// 返回 false 表示值得先平衡。
bool LocalScheduler::check_balance_dispatch(uint to_server_id, double MBpu){
    double beta_cost = 0, alpha_cost = 0;
    // raw balance cost
    uint max_row = 0;
    for (uint k = 0; k < gpu_n; k++){
        max_row = MAX(row_sum[to_server_id * gpu_n + k], max_row);
    }
    alpha_cost += intra_info.alpha;
    beta_cost += max_row * intra_info.beta * MBpu;

    // raw dispatch cost：按 spread_out 的对角线方式估算每轮最大值
    int largest_in_diagnal = 0;
    for (uint i = 0; i < gpu_n; i ++){
        largest_in_diagnal = 0;
        for (uint j = 0; j < gpu_n; j++){
            largest_in_diagnal = MAX(largest_in_diagnal, balanced_data[j][to_server_id * gpu_n + ((j + i) % gpu_n)]);
        }
        if (largest_in_diagnal != 0){
            alpha_cost += intra_info.alpha;
            beta_cost  += largest_in_diagnal * intra_info.beta * MBpu;
        }
    }
    // cout << "beta cost: " << beta_cost << ", alpha cost: " << alpha_cost << ", ratio: " << beta_cost / alpha_cost << endl;
    
    return (beta_cost / alpha_cost) < 2;

    // baseline: (m*n - 1) steps inter-server
    // initial fastall2all: m*n steps inter-server, each m steps intra-server
    // after optimization fastall2all: n steps inter-server, each m steps intra-server
}


// "逐阶段平衡"版本：把发往 to_server 的负载先调到 freq，再立刻提取 freq 的 dispatch。
// 用于 pipeline()（OPTION 1：每个置换阶段都重新平衡一次）。
// 步骤：
//   1. 找出低于 freq 的 GPU（smaller_row）和高于 freq 的 GPU（bigger_row）；
//   2. 把 big row 的数据按列搬到 small row，直到双方都接近 freq；
//      每搬一次记入 r.balance[big*gpu_n + small]（server 内 balance 流量）；
//   3. 每行再取出 freq 的数据准备发往目的 server，按列 j 记入
//      r.dispatch[i*gpu_n + j]（从本地 GPU i 搬到本地 GPU j，以对齐远端 GPU j）。
// 函数会修改 row_sum 和 balanced_data（扣除已经安排的数据）。
void LocalScheduler::server2server_balance(uint to_server_id, struct load_balance_result r, uint freq){
    vector<uint> smaller_row;
    vector<uint> bigger_row;
    for (uint i = 0; i < gpu_n; i++){
        if (row_sum[to_server_id * gpu_n + i] < freq){
            smaller_row.push_back(i);
        }else if (row_sum[to_server_id * gpu_n + i] > freq){
            bigger_row.push_back(i);
        }
    }

    for (auto small_row = smaller_row.begin(); small_row != smaller_row.end(); small_row ++){
        uint offset = freq - row_sum[to_server_id * gpu_n + *small_row];

        for (auto big_row = bigger_row.begin(); big_row != bigger_row.end(); ){

            for (uint j = 0; j < gpu_n; j++){
                // 从"超载"行搬一部分到"欠载"行：最多搬 min(超出的量, 缺口, 该列现有量)
                uint move_data = MIN(row_sum[to_server_id * gpu_n + *big_row] - freq, MIN(offset, balanced_data[*big_row][to_server_id * gpu_n + j]));
                offset -= move_data;
                // big row -> j ====> small row -> j via balance big row -> small row
                row_sum[to_server_id * gpu_n + *big_row] -= move_data;
                row_sum[to_server_id * gpu_n + *small_row] += move_data;
                balanced_data[*big_row][to_server_id * gpu_n + j] -= move_data;
                balanced_data[*small_row][to_server_id * gpu_n + j] += move_data;
                r.balance[ (*big_row) * gpu_n + (*small_row)] += move_data;
            }

            if (offset == 0){
                break;
            }

            if (row_sum[to_server_id * gpu_n + *big_row] == freq){
                big_row = bigger_row.erase(big_row);
            }else{
                big_row ++;
            }

        }
        if (bigger_row.empty()){
            break;
        }
    }

    uint * row_transfer = new uint[gpu_n];

    // 每块源 GPU 只取出 freq 的数据发往目的 server；
    // 数据原本要去远端 GPU j，但从本 GPU i 发出，所以 i != j 时需要在
    // server 内先从 GPU i 搬到 GPU j（记入 dispatch）。
    for (uint i = 0; i < gpu_n; i++){

        row_transfer[i] = 0;
        for (uint j = 0; j < gpu_n; j++){
            uint transfer = MIN(freq - row_transfer[i], balanced_data[i][to_server_id * gpu_n + j]);
            row_transfer[i] += transfer;
            balanced_data[i][to_server_id * gpu_n + j] -= transfer;
            row_sum[to_server_id * gpu_n + i] -= transfer;
            if (i != j){    // Transfer is Server m's GPUi --> Server n's GPUi, need to dispatch data from m's GPUi --> n's GPUj if i not equal j
                r.dispatch[i * gpu_n + j] += transfer;
            }
            if (row_transfer[i] == freq){
                break;
            }
        }

    }
    delete[] row_transfer;
}

// 提取一个阶段的 dispatch（不做平衡）：从每块 GPU 当前待发数据中取到
// 最多 freq 的量，按目的 GPU 记录 server 内需要搬动的数据。
// pipeline2 会在一次性 balance 之后，对每个置换集合调用这个函数。
void LocalScheduler::server2server_dispatch(uint to_server_id, struct load_balance_result r, uint freq){
    uint * row_transfer = new uint[gpu_n];

    for (uint i = 0; i < gpu_n; i++){

        row_transfer[i] = 0;
        for (uint j = 0; j < gpu_n; j++){
            uint transfer = MIN(freq - row_transfer[i], balanced_data[i][to_server_id * gpu_n + j]);
            row_transfer[i] += transfer;
            balanced_data[i][to_server_id * gpu_n + j] -= transfer;
            row_sum[to_server_id * gpu_n + i] -= transfer;
            if (i != j){    // Transfer is Server m's GPUi --> Server n's GPUi, need to dispatch data from m's GPUi --> n's GPUj if i not equal j
                r.dispatch[i * gpu_n + j] += transfer;
            }
            if (row_transfer[i] == freq){
                break;
            }
        }

    }
    delete[] row_transfer;
}

// 返回本 server 的 gpu_id 还欠 to_server 多少数据（平衡后、尚未 dispatch 的部分）。
// 供 pipeline_hooks_t 做研究统计：比如比较研究方案与原方案的剩余负载。
uint64_t LocalScheduler::pending_units(uint to_server_id, uint gpu_id) const {
    if (to_server_id >= server_n || gpu_id >= gpu_n)
        throw std::out_of_range("pending payload endpoint");
    uint64_t result = 0;
    for (uint j = 0; j < gpu_n; ++j)
        result += balanced_data[gpu_id][to_server_id * gpu_n + j];
    return result;
}


// "只平衡一次"版本：把发往 to_server 的各 GPU 负载调到 server2server_data
// 给出的平均值（pipeline2/pipeline3 使用）。只生成 balance 矩阵，
// 不提取 dispatch；dispatch 在之后的每个置换阶段单独生成。
// 移动方式与逐阶段版本相同：从超载行按列搬到欠载行，保证每列的目的 GPU 不变。
void LocalScheduler::server2server_balance(uint to_server_id, struct load_balance_result r){

    vector<uint> smaller_row;
    vector<uint> bigger_row;

    for (uint i = 0; i < gpu_n; i++){
        if (row_sum[to_server_id * gpu_n + i] < server2server_data[to_server_id]){
            smaller_row.push_back(i);
        }else if (row_sum[to_server_id * gpu_n + i] > server2server_data[to_server_id]){
            bigger_row.push_back(i);
        }
    }

    for (auto big_row = bigger_row.begin(); big_row != bigger_row.end(); big_row++){

        uint rm_data = row_sum[to_server_id * gpu_n + *big_row] - server2server_data[to_server_id];
        for (auto small_row = smaller_row.begin(); small_row != smaller_row.end();){
            for (uint j = 0; j < gpu_n; j++){
                // 检查 big row 的每一列：能搬多少 = min(还差多少搬完, 该列现有量, small row 还缺多少)
                uint mv_data = MIN(MIN(rm_data, balanced_data[*big_row][to_server_id * gpu_n + j]), server2server_data[to_server_id] - row_sum[to_server_id * gpu_n + *small_row]);
                rm_data -= mv_data;
                // big row -> j ====> small row -> j via balance big row -> small row
                row_sum[to_server_id * gpu_n + *small_row] += mv_data;
                row_sum[to_server_id * gpu_n + *big_row] -= mv_data;
                balanced_data[*big_row][to_server_id * gpu_n + j] -= mv_data;
                balanced_data[*small_row][to_server_id * gpu_n + j] += mv_data;
                r.balance[ (*big_row) * gpu_n + (*small_row)] += mv_data;
            }
            if (rm_data == 0){
                break;
            }

            if (row_sum[to_server_id * gpu_n + *small_row] == server2server_data[to_server_id]){
                small_row = smaller_row.erase(small_row);
            }else{
                small_row ++;
            }
        }

        if (smaller_row.empty()){
            break;
        }
    }
}


void LocalScheduler::print_load_balance_step(struct load_balance_result *r){
    cout << "balance matrix: " << endl;
    for (uint i = 0; i < gpu_n; i++){
        for (uint j = 0; j < gpu_n; j++){
            cout << setw(10);
            cout << r->balance[i * gpu_n + j];
        }
        cout << endl;
    }
    cout << "dispatch matrix: " << endl;
    for (uint i = 0; i < gpu_n; i++){
        for (uint j = 0; j < gpu_n; j++){
            cout << setw(10);
            cout << r->dispatch[i * gpu_n + j];
        }
        cout << endl;
    }
}

void LocalScheduler::print(uint dst_server_id){
    cout << "server "<< server_id << " to server " << dst_server_id << endl;
    for (uint i = 0; i < gpu_n; i++){
        for (uint j = 0; j < gpu_n; j++){
            cout << setw(10);
            cout << data[i][dst_server_id * gpu_n + j];
        }
        cout << endl;
    }
}


void LocalScheduler::print(){
    uint dim = gpu_n * server_n;

    cout << "original matrix: " << endl;
    for (uint i = 0; i < gpu_n; i++){
        for (uint j = 0; j < dim; j++){
            cout << setw(10);
            cout << data[i][j];
        }
        cout << endl;
    }

    // cout << endl << "balanced matrix: " << endl;
    // for (uint i = 0; i < gpu_n; i++){
    //     for (uint j = 0; j < dim; j++){
    //         cout << setw(10);
    //         cout << balanced_data[i][j];
    //     }
    //     cout << endl;
    // }

    // cout << endl << "----------------" << endl << "Intra All2All" << endl;
    // for (auto it = intra_all2all.begin(); it != intra_all2all.end(); it++){
    //     cout << "server " << server_id << " to server " << it->first << endl;
    //     cout << "balance:" << endl;
    //     for (uint i = 0; i < gpu_n; i++){
    //         for (uint j = 0; j < gpu_n; j++){
    //             cout << setw(10);
    //             cout << it->second.balance[i*gpu_n + j];
    //         }
    //         cout << endl;
    //     }
    //     cout << endl<< "dispatch:" << endl;
    //     for (uint i = 0; i < gpu_n; i++){
    //         for (uint j = 0; j < gpu_n; j++){
    //             cout << setw(10);
    //             cout << it->second.dispatch[i*gpu_n + j];
    //         }
    //         cout << endl;
    //     }
    //     cout << endl;
    // }
}
