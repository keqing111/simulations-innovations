#include <iostream>
#include "matrix.h"
#include "all2all.h"
#include "local.h"
#include "global.h"
#include "test.h"
#include "config.h"


using namespace std;

// ============================================================================
// FAST 仿真器入口。
//
// FastAll2AllTester 的构造参数：
//   (server_n, gpu_n, test_times, enable_scaling, inter_link_type, intra_link_type)
// 下面这行表示：2 个 server、每个 16 块 GPU、重复 20 次、默认不缩放、
// server 间用 400G 以太网、server 内用 H100 级别的 NVLink。
//
// 可选实验（取消注释即可切换）：
//   run()                       一次或多次端到端对比（FAST vs Spread-out）；
//   server_gpu_number_benchmark 扫描 server/GPU 规模，输出加速比、耗时、算法带宽；
//   fabric_speed_benchmark      固定 inter 带宽，扫描 server 内带宽；
//   topology_benchmark          用指定 server 内拓扑代价模型评估；
//   skewness_benchmark          用 Zipf 分布扫描流量偏斜度；
//   transfer_size_benchmark     扫描单条消息大小，并可开关 scaling。
// 当前默认跑 server_gpu_number_benchmark(H100, ETHER100)。
// 其余注释块是保留的小样例/调试代码，也可以当作 API 用法示例。
// ============================================================================

int main(){

    // uint mat_a[] = {
    //     100,100,100,
    //     177,142,1323,
    //     1234,34,344,
    // };

    // Matrix m(mat_a,3);
    // m.get_sdsm_info();
    // m.print();
    // FastAll2All app(&m);
    // app.to_scaled_matrix();
    // app.to_scaled_doubly_stochastic_matrix();
    // app.print();

    // Decomposition tester
    // vector<uint> test_dim = {10, 50, 100, 200};
    // DecompositionTester tester(test_dim);
    // tester.run(true);

    // uint mat_b[] = {
    //     100,100,100, 1, 2, 3, 10, 11, 12,
    //     200,200,200, 4, 5, 6, 13, 14, 15,
    //     300,300,300, 7, 8, 9, 16, 17, 18
    // };
    // LocalScheduler l(mat_b, 3, 3, 0);
    // l.load_balance();
    // l.print();

    // 构造实验对象：2 个 server、每 server 16 GPU、重复 20 次、关闭 scaling，
    // 默认链路为 400G 以太网 + H100 级 server 内互连。
    FastAll2AllTester simulator(2, 16, 20, false, ETHER400, H100);
    // simulator.run();
    // 当前实验：扫描 server/GPU 规模；这里显式传入 H100 内连 + 100G 以太网。
    simulator.server_gpu_number_benchmark(H100, ETHER100);
    // simulator.fabric_speed_benchmark();
    
    // A100
    // simulator.topology_benchmark(&spread_out, 100, 7200);
    // // MI300
    // simulator.topology_benchmark(&intra_transfer_full_mesh, 100, 1024);
    // // MI250
    // simulator.topology_benchmark(&intra_transfer_2ring, 100, 1800);
    // // V100
    // simulator.topology_benchmark(&intra_transfer_hybrid_cude_mesh, 100, 1200);


    // simulator.skewness_benchmark();
    // simulator.transfer_size_benchmark(false, FAST, INFB, &balance_alpha_beta, "0");
    // simulator.transfer_size_benchmark(true, FAST, INFB, &balance_alpha_beta,  "1");
    // simulator.transfer_size_benchmark(true, FAST, INFB, &always_scale, "2");


    
    return 0;
}