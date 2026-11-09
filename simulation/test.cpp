#include "test.h"
#include "matrix.h"
#include "all2all.h"
#include "define.h"
#include "local.h"
#include "global.h"
#include <chrono>
#include <fstream>
#include <vector>
#include <map>
#include <cmath>
using namespace std;
using namespace chrono;

// ============================================================================
// 实验驱动的实现。
//
// 矩阵元素与物理量：
//   - 默认 1 个元素 = 1 个"单位"，通过 MBpu（MB per unit）换算成 MB；
//   - 传输时间由 alpha + 元素数 * MBpu * beta 得到（beta 单位 us/MB）；
//   - 算法带宽 algbw = 总工作量 / 模型时间 / GPU 数，再乘 1e3 换算单位。
//
// 注意区分两类"时间"：
//   - fastall2all_r.t：模型估算的端到端时间（网络传输 + 未被重叠的 server 内开销，
//     仿真的核心输出）；
//   - cur_time：在本机跑完整调度算法实际花的 CPU 时间（wall clock），
//     用来观察调度器的计算开销，不是网络时间。
// ============================================================================

// 只测分解：对每个维度生成随机矩阵，分别计时
//   建矩阵 / scaling（可选）/ 双随机化 / Birkhoff 分解，
// 并用 verify_decomposition() 校验分解结果能否还原"双随机化后的矩阵"
// （不是原始随机矩阵——双随机化会补入流量）。
void DecompositionTester::run(bool enable_scaling){

    for (auto test_dim = test_dims.begin(); test_dim != test_dims.end(); test_dim++){
        uint dim = *test_dim;
        cout << "---------------------------------------------" << endl;;
        cout << "Testing " << dim << "x" << dim <<" matrices: " << endl;;

        uint* mat = new uint[dim * dim];
        auto duration = 0;
        for (uint i = 0; i < times_per_dim; i++){
            cout <<"\t" << i <<":";
            for (uint m = 0; m < dim; m++){
                for (uint n = 0; n < dim; n++){
                    uint id = m * dim + n;
                    mat[id] = rand() % MAX_ELEMENT;
                }
            }
            // 分阶段计时：建对象 -> scaling -> 双随机化 -> 分解
            auto t1 = high_resolution_clock::now();
            Matrix m(mat, dim);
            FastAll2All app(&m, dim);
            auto t2 = high_resolution_clock::now();
            if (enable_scaling){
                app.to_scaled_matrix(&balance_alpha_beta);
            }
            auto t3 = high_resolution_clock::now();
            app.to_scaled_doubly_stochastic_matrix();
            auto t4 = high_resolution_clock::now();
            app.decompose();
            auto t5 = high_resolution_clock::now();
            duration += duration_cast<microseconds>(t5 - t1).count();
            if (app.verify_decomposition()){
                cout << "t1-t2: " << duration_cast<microseconds>(t2 - t1).count() <<" us, " 
                    "t2-t3: " << duration_cast<microseconds>(t3- t2).count() <<" us, " 
                    "t3-t4: " << duration_cast<microseconds>(t4 - t3).count() <<" us, "
                    "t4-t5: " << duration_cast<microseconds>(t5 - t4).count() <<" us" <<endl;
            }else{
                LOG("decomposition verifcation failed");
                app.print_decomposition();
                exit(1);
            }

        }
        delete[] mat;
        cout << "Avg time cost: " << duration / times_per_dim << " us" << endl;
    }
}


// 构造 benchmark 上下文并设置随机种子。
//   s_n / g_n       ：server 数和每 server GPU 数
//   times           ：每个配置点重复次数
//   scaling         ：是否允许 FastAll2All 做矩阵缩放
//   inter/intra_type：默认链路类型（benchmark 方法可以覆盖）
FastAll2AllTester::FastAll2AllTester(uint s_n, uint g_n, uint times, bool scaling, INTER_LINK_TYPE inter_type, INTRA_LINK_TYPE intra_type){
    srand((unsigned)time(0));
    server_n = s_n;
    gpu_n = g_n;
    test_times = times;
    enable_scaling = scaling;
    inter_link_type = inter_type;
    intra_link_type = intra_type;
}

// 最简单的端到端对比：固定规模，重复 test_times 次。
// 每次随机生成流量，跑 FAST（pipeline2），再和 Spread-out 基线比加速比。
void FastAll2AllTester::run(){
    uint dim = server_n * gpu_n;        // 总共 rank 数
    uint* mat = new uint[dim * dim];

        // 这部分随机数据生成有待商榷，因为完全随机，很有可能和现实情况脱节。zipf 分布呢？
    for (uint t = 0; t < test_times; t++){      // 总共 test_time 轮
        // randomly generate data
        for (uint i = 0; i < dim; i++){
            for (uint j = 0; j < dim; j++){
                mat[i * dim + j] = rand() % MAX_ELEMENT;            // 最大流量数据大小
            }
        }
        for (uint i = 0; i < dim; i++){
            mat[i * dim + i] = 0;                 // 自己发给自己的流量没有意义，清掉对角线
        }

        // print_matrix(mat, dim, dim);

        // FastAll2All
        cout << "++++++++++++ FAST ALL2ALL ++++++++++++"<<endl;
        // 每个源 server 一个 LocalScheduler；mat + s*dim*gpu_n 指向该 server 的第一行
        // localscheduler 只得到本机的发送矩阵，没有接受？
        vector<LocalScheduler*> local_schedulers;
        for (uint s = 0; s < server_n; s++){
            LocalScheduler* ls = new LocalScheduler(mat + s * dim * gpu_n, gpu_n, server_n, s, intra_link_type);
            local_schedulers.push_back(ls);
        }
            // global scheduler 的功能是？
        GlobalScheduler global_scheduler(server_n, gpu_n, local_schedulers, inter_link_type, intra_link_type, enable_scaling);
        // pipeline2 是怎么计算的？
        struct pipeline_result_t fastall2all_r = global_scheduler.pipeline2();

        for (auto ls = local_schedulers.begin(); ls != local_schedulers.end(); ls++){
            delete (*ls);
        }

        cout << "++++++++++++ BASELINE ALL2ALL ++++++++++++"<<endl;
        // Baseline - Spreadout：经典 all-to-all 的耗时模型
        // 这个 spreadout 实现每轮都累加最长耗时的 server 内 gpu，和实际对比耗时会长不少
        double baseline = spread_out_baseline(mat, server_n, gpu_n, get_inter_link_info(inter_link_type), get_intra_link_info(intra_link_type));
        cout << "Baseline All2All: " << baseline << endl;

        cout << "SPEEDUP: " << baseline / fastall2all_r.t << endl;
    }

    delete[] mat;
}


// 扫描 "server 数 × 每 server GPU 数" 的影响。
// 固定 g_n=8，server 数从 2 到 64（步长 2），每个配置重复 test_times 次。
// 输出三个文件：
//   speedup_server_gpu_number.txt  每个配置的加速比（Spread-out 时间 / FAST 时间）
//   time_server_gpu_number.txt     每次调度在本机实际消耗的 CPU 时间
//   sim_scale.txt                  三种算法带宽（FAST / 理论最优 / Spread-out）
void FastAll2AllTester::server_gpu_number_benchmark(INTRA_LINK_TYPE _intra_type, INTER_LINK_TYPE _inter_type){

    cout << "Benchmarking the effect of server and gpu number" <<endl;
    map<struct server_gpu_config_t, struct statistics_t> algo_speedup;
    map<struct server_gpu_config_t, struct statistics_t> algo_time;
    map<struct server_gpu_config_t, struct simulation_result_t> algbws;


    for (uint s_n = 2; s_n <= 64; s_n += 2){
        for (uint g_n = 8; g_n <= 8; g_n *= 2){     // g_n 是固定的，为什么？
            vector <double> cur_speedup;
            vector <double> cur_time;
            vector <double> cur_flash_algbw;
            vector <double> cur_spread_algbw;
            vector <double> cur_opt_algbw;

            struct server_gpu_config_t cur_config = {.svr_n=s_n, .gpu_n=g_n};

            for (uint tt = 0; tt < test_times; tt++){       // 所以 test_time 是全局参数
                uint dim = s_n * g_n;
                uint* mat = new uint[dim * dim];
                // randomly generate data
                uint workload_sz = 0;
                for (uint i = 0; i < dim; i++){
                    for (uint j = 0; j < dim; j++){
                        if (i == j){
                            mat[i * dim + j] = 0;
                            continue;
                        }
                        mat[i * dim + j] = rand() % MAX_ELEMENT;            // 依然是纯随机流量矩阵生成，不能这样
                        workload_sz += mat[i * dim + j];
                    }
                }
                uint max_cross_node_send_sz = 0, max_cross_node_recv_sz = 0;        // 代表什么？最大跨 server 收发数据 size
                for (uint i = 0; i < s_n; i++){
                    uint cross_node_send_sz = 0;
                    uint cross_node_recv_sz = 0;
                    for (uint j = 0; j < g_n; j++){
                        for (uint z = 0; z < g_n * s_n; z++){
                            if (i != z / g_n){
                                cross_node_send_sz += mat[(i * g_n + j) * dim + z];
                                cross_node_recv_sz += mat[z * dim + (i * g_n + j)];
                            }
                        }
                    }
                    max_cross_node_send_sz = MAX(cross_node_send_sz, max_cross_node_send_sz);
                    max_cross_node_recv_sz = MAX(max_cross_node_recv_sz, cross_node_recv_sz);
                }

                // FastAll2All
                vector<LocalScheduler*> local_schedulers;
                for (uint s = 0; s < s_n; s++){
                    LocalScheduler* ls = new LocalScheduler(mat + s * dim * g_n, g_n, s_n, s, _intra_type);
                    local_schedulers.push_back(ls);
                }

                GlobalScheduler global_scheduler(s_n, g_n, local_schedulers, _inter_type, _intra_type, enable_scaling);

                // 跑 FAST 并记录"调度算法本身"的 CPU 耗时（不是网络时间）
                auto t1 = high_resolution_clock::now();
                struct pipeline_result_t fastall2all_r = global_scheduler.pipeline2();
                auto t2 = high_resolution_clock::now();
                // 如果这不是异步执行的话，t2-t1 就应该是包含整个调度算法仿真计算通信结果的时间而不是纯调度？
                auto duration = duration_cast<microseconds>(t2 - t1).count();
                cur_time.push_back(duration);


                for (auto ls = local_schedulers.begin(); ls != local_schedulers.end(); ls++){
                    delete (*ls);
                }

                // Baseline - Spreadout
                double baseline = spread_out_baseline(mat, s_n, g_n, get_inter_link_info(_inter_type), get_intra_link_info(_intra_type));
                cur_speedup.push_back(baseline/fastall2all_r.t);
                // 算法带宽 = 总工作量 / 时间 / GPU 数。
                // 工作量按 MB 计、时间按 us 计时，MB/us * 1e3 = GB/s。
                double spreadout_algbw = workload_sz / baseline * 1e3 / (s_n * g_n);
                cur_spread_algbw.push_back(spreadout_algbw);

                // 理论最优上界：把最忙 server 的跨 server 总流量平均分摊到
                // g_n 块 GPU 的网卡上，用 inter.beta 估计所需传输时间，再反推带宽。
                // 它对时间是一个宽松下界（忽略 server 内开销和负载不均），
                // 因此反推出的带宽是理论上界。
                // cout << "workload sz: " << workload_sz << endl;
                // cout << "cross node sz: " << MAX(max_cross_node_send_sz, max_cross_node_recv_sz)  << endl;
                double crossnode_time_limit = MAX(max_cross_node_send_sz, max_cross_node_recv_sz) * get_inter_link_info(_inter_type).beta / g_n;
                double opt_algbw_limit = workload_sz  * 1e3 / (g_n * s_n) / crossnode_time_limit;
                cur_opt_algbw.push_back(opt_algbw_limit);

                // FAST 的算法带宽：用模型时间 fastall2all_r.t
                double flash_algbw = workload_sz / fastall2all_r.t * 1e3 / (s_n * g_n);
                cur_flash_algbw.push_back(flash_algbw);
                // std::cout << "flash algbw: " << flash_algbw  << " GBps" << std::endl;
                // std::cout << "optimal algbw: " << opt_algbw_limit  << " GBps" << std::endl;
                // std::cout << "spreadout algbw: " << spreadout_algbw  << " GBps" << std::endl;
                // std::cout << s_n << " " << flash_algbw << " " << opt_algbw_limit << " " << spreadout_algbw << std::endl;

                delete[] mat;
            }


            double speedup_avg, speedup_sd;
            compute_average_standard_deviation(&cur_speedup, &speedup_avg, &speedup_sd);
            struct statistics_t speedup_r = {.avg = speedup_avg, .err = speedup_sd / sqrt(test_times)};
            algo_speedup.insert(make_pair(cur_config, speedup_r));

            double time_avg, time_sd;
            compute_average_standard_deviation(&cur_time, &time_avg, &time_sd);
            struct statistics_t time_r = {.avg = time_avg, .err = time_sd / sqrt(test_times)};
            algo_time.insert(make_pair(cur_config, time_r));

            double flash_avg, flash_sd;
            compute_average_standard_deviation(&cur_flash_algbw, &flash_avg, &flash_sd);
            double spread_avg, spread_sd;
            compute_average_standard_deviation(&cur_spread_algbw, &spread_avg, &spread_sd);
            double opt_avg, opt_sd;
            compute_average_standard_deviation(&cur_opt_algbw, &opt_avg, &opt_sd);
            struct simulation_result_t sim_r = {.flash_algbw = flash_avg, .opt_algbw = opt_avg, .spread_algbw = spread_avg};

            algbws.insert(make_pair(cur_config, sim_r));
        }

    }

    // 把结果写到文本文件（列格式与 plot/ 下的 Python 脚本约定一致），
    // 同时打印到终端方便直接观察。
    // output benchmark results to files
    string benchmark_dir(BENCHMARK_DIR);

    ofstream speedup_f;
    speedup_f.open(benchmark_dir + "speedup_server_gpu_number.txt");
    cout << " ----------------- SPEEDUP -----------------" << endl;
    for (auto sr = algo_speedup.begin(); sr != algo_speedup.end(); sr++){
        speedup_f << sr->first.svr_n << " " << sr->first.gpu_n << " " << sr->second.avg << " " << sr->second.err << endl;
        cout << sr->first.svr_n << " " << sr->first.gpu_n << " " << sr->second.avg << " " << sr->second.err << endl;
    }
    speedup_f.close();

    ofstream time_f;
    time_f.open(benchmark_dir + "time_server_gpu_number.txt");
    cout << " ----------------- TIME COST -----------------" << endl;
    for (auto tr = algo_time.begin(); tr != algo_time.end(); tr++){
        time_f << tr->first.svr_n << " " << tr->first.gpu_n << " " << tr->second.avg << " " << tr->second.err << endl;
        cout << tr->first.svr_n << " " << tr->first.gpu_n << " " << tr->second.avg << " us, " << tr->second.err << endl;
    }
    time_f.close();

    ofstream scale_f;
    scale_f.open(benchmark_dir + "sim_scale.txt");
    cout << " ----------------- AlgBW -----------------" << endl;
    for (auto tr = algbws.begin(); tr != algbws.end(); tr++){
        scale_f << tr->first.svr_n << " " << tr->first.gpu_n << " " << tr->second.flash_algbw << " " << tr->second.opt_algbw << " " << tr->second.spread_algbw << endl;
        cout << tr->first.svr_n << " " << tr->first.gpu_n << " " << tr->second.flash_algbw << " " << tr->second.opt_algbw << " " << tr->second.spread_algbw << endl;
    }
    scale_f.close();
}

// 评估 server 内拓扑的影响：固定 4 个 server × 8 GPU，把 server 内代价函数
// 换成调用者提供的 fn（如 full mesh / 2 ring / hybrid cube mesh），
// 其余流程与 pipeline3 一致。只打印结果，不写文件。
// inter_Gbps / intra_Gbps 用来把链路带宽换算成 beta（us/MB）。
void FastAll2AllTester::topology_benchmark(intra_transfer_topo_fn fn, uint inter_Gbps, uint intra_Gbps){
    cout << "Benchmarking topology" <<endl;

    map<server_speed_config_t, struct statistics_t> algo_speedup;    // intra-link speed in Gbps => speedup
    map<server_speed_config_t, struct simulation_result_t> algbws;


    struct link_info_t inter_INFB = get_inter_link_info(INFB), intra_DGX2 = get_intra_link_info(DGX2);
    struct link_info_t inter_link = {.tput=0,.alpha=inter_INFB.alpha, .beta=Gbps_to_us_per_MB(inter_Gbps)};
    struct link_info_t intra_link = {.tput=0,.alpha=intra_DGX2.alpha, .beta=Gbps_to_us_per_MB(intra_Gbps)};
    uint g_n = 8, s_n = 4; 


    for (uint tt = 0; tt < test_times; tt++){
        uint dim = s_n * g_n;
        uint* mat = new uint[dim * dim];
        // randomly generate data
        uint workload_sz = 0;
        for (uint i = 0; i < dim; i++){
            for (uint j = 0; j < dim; j++){
                if (i == j){
                    mat[i * dim + j] = 0;
                    continue;
                }
                mat[i * dim + j] = rand() % MAX_ELEMENT;
                workload_sz += mat[i * dim + j];
            }
        }
        uint intra_node_sz = 0;
        uint max_cross_node_send_sz = 0, max_cross_node_recv_sz = 0;
        for (uint i = 0; i < s_n; i++){
            uint cross_node_send_sz = 0;
            uint cross_node_recv_sz = 0;
            for (uint j = 0; j < g_n; j++){
                for (uint z = 0; z < g_n * s_n; z++){
                    if (i != z / g_n){
                        cross_node_send_sz += mat[(i * g_n + j) * dim + z];
                        cross_node_recv_sz += mat[z * dim + (i * g_n + j)];
                    }else{
                        intra_node_sz += mat[(i * g_n + j) * dim + z];
                    }
                }
            }
            max_cross_node_send_sz = MAX(cross_node_send_sz, max_cross_node_send_sz);
            max_cross_node_recv_sz = MAX(max_cross_node_recv_sz, cross_node_recv_sz);
        }

        // FastAll2All
        vector<LocalScheduler*> local_schedulers;
        for (uint s = 0; s < s_n; s++){
            LocalScheduler* ls = new LocalScheduler(mat + s * dim * g_n, g_n, s_n, s, intra_link);
            local_schedulers.push_back(ls);
        }

        GlobalScheduler global_scheduler(s_n, g_n, local_schedulers, inter_link, intra_link, enable_scaling);

        struct pipeline_result_t fastall2all_r = global_scheduler.pipeline3(1.0, fn);


        for (auto ls = local_schedulers.begin(); ls != local_schedulers.end(); ls++){
            delete (*ls);
        }
        // Baseline - Spreadout
        double baseline = spread_out_baseline(mat, s_n, g_n, inter_link, intra_link);
        double spreadout_algbw = workload_sz / baseline * 1e3 / (s_n * g_n);

        // calculate theoretical optimal
        double crossnode_time_limit = MAX(max_cross_node_send_sz, max_cross_node_recv_sz) * inter_link.beta / g_n;
        double opt_algbw_limit = workload_sz  * 1e3 / (g_n * s_n) / crossnode_time_limit;

        double flash_algbw = workload_sz / fastall2all_r.t * 1e3 / (s_n * g_n);

        std::cout << "flash algbw: " << flash_algbw << " GBps, optimal algbw: " << opt_algbw_limit << " GBps, Spreadout: " << spreadout_algbw << " GBps" << std::endl;
        delete[] mat;

    }    
}



// 扫描 server 内链路带宽对 FAST 的影响。
// 固定 server 间 100 Gbps，server 内带宽从 100 到 7000 Gbps；
// server 数取 4、8、16。输出加速比、intra 开销比例和算法带宽。
void FastAll2AllTester::fabric_speed_benchmark(){

    cout << "Benchmarking the effect of intra-link speed" <<endl;

    map<server_speed_config_t, struct statistics_t> algo_speedup;    // intra-link speed in Gbps => speedup
    map<server_speed_config_t, struct statistics_t> algo_intra_ratio;
    map<server_speed_config_t, struct simulation_result_t> algbws;

    vector <uint> link_speeds = {100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000, 2250, 2500, 2750, 3000, 3500, 4000, 5000, 6000, 7000};
    uint inter_speed = 100; // Gbps
    uint g_n = 8;  // fix the number of GPU

    struct link_info_t inter_INFB = get_inter_link_info(INFB), intra_DGX2 = get_intra_link_info(DGX2);
    struct link_info_t inter_link = {.tput=0,.alpha=inter_INFB.alpha, .beta=Gbps_to_us_per_MB(inter_speed)};

    for (auto intra_speed = link_speeds.begin(); intra_speed != link_speeds.end(); intra_speed++){

        for (uint s_n = 4; s_n <=16; s_n *=2){
            struct server_speed_config_t cur_config = {.svr_n = s_n, .speed = *intra_speed, .ratio =  (double)(*intra_speed) / inter_speed};
            struct link_info_t intra_link = {.tput=0,.alpha=intra_DGX2.alpha, .beta=Gbps_to_us_per_MB(*intra_speed)};
            // cout << "intra link - alpha: " << intra_link.alpha << " - beta: " << intra_link.beta << endl;
            // cout << "inter link - alpha: " << inter_link.alpha << " - beta: " << inter_link.beta << endl;
            vector <double> cur_speedup;
            vector <double> cur_ratio;
            vector <double> cur_flash_algbw;
            vector <double> cur_spread_algbw;
            vector <double> cur_opt_algbw;


            for (uint tt = 0; tt < test_times; tt++){
                uint dim = s_n * g_n;
                uint* mat = new uint[dim * dim];
                // randomly generate data
                uint workload_sz = 0;
                for (uint i = 0; i < dim; i++){
                    for (uint j = 0; j < dim; j++){
                        if (i == j){
                            mat[i * dim + j] = 0;
                            continue;
                        }
                        mat[i * dim + j] = rand() % MAX_ELEMENT;
                        workload_sz += mat[i * dim + j];
                    }
                }
                uint intra_node_sz = 0;
                uint max_cross_node_send_sz = 0, max_cross_node_recv_sz = 0;
                for (uint i = 0; i < s_n; i++){
                    uint cross_node_send_sz = 0;
                    uint cross_node_recv_sz = 0;
                    for (uint j = 0; j < g_n; j++){
                        for (uint z = 0; z < g_n * s_n; z++){
                            if (i != z / g_n){
                                cross_node_send_sz += mat[(i * g_n + j) * dim + z];
                                cross_node_recv_sz += mat[z * dim + (i * g_n + j)];
                            }else{
                                intra_node_sz += mat[(i * g_n + j) * dim + z];
                            }
                        }
                    }
                    max_cross_node_send_sz = MAX(cross_node_send_sz, max_cross_node_send_sz);
                    max_cross_node_recv_sz = MAX(max_cross_node_recv_sz, cross_node_recv_sz);
                }

                // FastAll2All
                vector<LocalScheduler*> local_schedulers;
                for (uint s = 0; s < s_n; s++){
                    LocalScheduler* ls = new LocalScheduler(mat + s * dim * g_n, g_n, s_n, s, intra_link);
                    local_schedulers.push_back(ls);
                }

                GlobalScheduler global_scheduler(s_n, g_n, local_schedulers, inter_link, intra_link, enable_scaling);

                struct pipeline_result_t fastall2all_r = global_scheduler.pipeline2();


                for (auto ls = local_schedulers.begin(); ls != local_schedulers.end(); ls++){
                    delete (*ls);
                }

                // Baseline - Spreadout
                double baseline = spread_out_baseline(mat, s_n, g_n, inter_link, intra_link);
                cur_speedup.push_back(baseline/fastall2all_r.t);
                cur_ratio.push_back(fastall2all_r.ratio);
                double spreadout_algbw = workload_sz / baseline * 1e3 / (s_n * g_n);
                cur_spread_algbw.push_back(spreadout_algbw);

                // calculate theoretical optimal
                double crossnode_time_limit = MAX(MAX(max_cross_node_send_sz, max_cross_node_recv_sz) * inter_link.beta / g_n, intra_node_sz * intra_link.beta / g_n);
                double opt_algbw_limit = workload_sz  * 1e3 / (g_n * s_n) / crossnode_time_limit;
                cur_opt_algbw.push_back(opt_algbw_limit);

                double flash_algbw = workload_sz / fastall2all_r.t * 1e3 / (s_n * g_n);
                cur_flash_algbw.push_back(flash_algbw);
       
                delete[] mat;
        
            }
            double speedup_avg, speedup_sd;
            compute_average_standard_deviation(&cur_speedup, &speedup_avg, &speedup_sd);
            struct statistics_t speedup_r = {.avg = speedup_avg, .err = speedup_sd / sqrt(test_times)};
            algo_speedup.insert(make_pair(cur_config, speedup_r));

            double ratio_avg, ratio_sd;
            compute_average_standard_deviation(&cur_ratio, &ratio_avg, &ratio_sd);
            struct statistics_t ratio_r = {.avg = ratio_avg, .err = ratio_sd / sqrt(test_times)};
            algo_intra_ratio.insert(make_pair(cur_config, ratio_r));

            double flash_avg, flash_sd;
            compute_average_standard_deviation(&cur_flash_algbw, &flash_avg, &flash_sd);
            double spread_avg, spread_sd;
            compute_average_standard_deviation(&cur_spread_algbw, &spread_avg, &spread_sd);
            double opt_avg, opt_sd;
            compute_average_standard_deviation(&cur_opt_algbw, &opt_avg, &opt_sd);
            struct simulation_result_t sim_r = {.flash_algbw = flash_avg, .opt_algbw = opt_avg, .spread_algbw = spread_avg};

            algbws.insert(make_pair(cur_config, sim_r));

        }

        
    }

    // 把结果写到文本文件（列格式与 plot/ 下的 Python 脚本约定一致），
    // 同时打印到终端方便直接观察。
    // output benchmark results to files
    string benchmark_dir(BENCHMARK_DIR);

    ofstream speedup_f;
    speedup_f.open(benchmark_dir + "speedup_intra_speed.txt");
    cout << " ----------------- SPEEDUP -----------------" << endl;
    for (auto sr = algo_speedup.begin(); sr != algo_speedup.end(); sr++){
        speedup_f << sr->first.svr_n << " " << sr->first.speed << " " << sr->second.avg << " " << sr->second.err << endl;
        cout << sr->first.svr_n << " " << sr->first.speed << " " << sr->second.avg << " " << sr->second.err << endl;
    }
    speedup_f.close();

    ofstream ratio_f;
    ratio_f.open(benchmark_dir + "intra_ratio_intra_speed.txt");
    cout << " ----------------- RATIO -----------------" << endl;
    for (auto rr = algo_intra_ratio.begin(); rr != algo_intra_ratio.end(); rr++){
        ratio_f << rr->first.svr_n<< " " << rr->first.speed << " " << rr->second.avg << " " << rr->second.err << endl;
        cout << rr->first.svr_n<< " " << rr->first.speed << " " << rr->second.avg << " " << rr->second.err << endl;
    }
    ratio_f.close();

    // sim_intra_speed.txt 中存的是 algbw / 12.5，即相对 100 Gbps 网卡
    // （12.5 GB/s）的归一化带宽；终端打印的是原始 GB/s。
    ofstream scale_f;
    scale_f.open(benchmark_dir + "sim_intra_speed.txt");
    cout << " ----------------- AlgBW -----------------" << endl;
    for (auto tr = algbws.begin(); tr != algbws.end(); tr++){
        scale_f << tr->first.svr_n << " " << tr->first.ratio << " " << tr->second.flash_algbw / 12.5 << " " << tr->second.opt_algbw / 12.5 << " " << tr->second.spread_algbw / 12.5 << endl;
        cout << tr->first.svr_n << " " << tr->first.ratio << " " << tr->second.flash_algbw << " " << tr->second.opt_algbw << " " << tr->second.spread_algbw << endl;
    }
    scale_f.close();

}


// 扫描流量偏斜度：用 Zipf(s) 生成矩阵，s 越大表示少数流占越大比例。
// 固定 16 GPU/server，server 数 16/32/48/64。
// 主要看 FAST 相对 Spread-out 的加速比随偏斜度的变化。
void FastAll2AllTester::skewness_benchmark(INTRA_LINK_TYPE _intra_type, INTER_LINK_TYPE _inter_type){
    cout << "Benchmarking the effect of skewness" <<endl;
    map<struct server_skewness_config_t, struct statistics_t> algo_speedup;
    map<struct server_skewness_config_t, struct statistics_t> algo_time;

    uint g_n = 16;  // fix the number of GPU

    vector <double> skewness = {0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999};

    for (uint s_n = 64; s_n >=16; s_n -=16){
        for (auto skew = skewness.begin(); skew != skewness.end(); skew ++){
            vector <double> cur_speedup;
            vector <double> cur_time;
            struct server_skewness_config_t cur_config = {.svr_n=s_n, .s=*skew};
            zipf_distribution zipf(*skew);
            vector<uint> zipf_r;

            for (uint tt = 0; tt < test_times; tt++){
                uint dim = s_n * g_n;
                uint* mat = new uint[dim * dim];
                zipf_r.clear();
                zipf.zipf(&zipf_r, dim * dim);
                // zipf generate data
                for (uint i = 0; i < dim; i++){
                    for (uint j = 0; j < dim; j++){
                        mat[i * dim + j] = zipf_r[i * dim + j];
                    }
                }
                for (uint i = 0; i < dim; i++){
                    mat[i * dim + i] = 0;                 //clean the diagnal
                }

                // FastAll2All
                vector<LocalScheduler*> local_schedulers;
                for (uint s = 0; s < s_n; s++){
                    LocalScheduler* ls = new LocalScheduler(mat + s * dim * g_n, g_n, s_n, s, _intra_type);
                    local_schedulers.push_back(ls);
                }

                GlobalScheduler global_scheduler(s_n, g_n, local_schedulers, _inter_type, _intra_type, enable_scaling);

                auto t1 = high_resolution_clock::now();   
                struct pipeline_result_t fastall2all_r = global_scheduler.pipeline2();
                auto t2 = high_resolution_clock::now();     
                auto duration = duration_cast<microseconds>(t2 - t1).count();
                // cout << "pipeline time: " << duration << endl;
                cur_time.push_back(duration);


                for (auto ls = local_schedulers.begin(); ls != local_schedulers.end(); ls++){
                    delete (*ls);
                }

                // Baseline - Spreadout
                double baseline = spread_out_baseline(mat, s_n, g_n, get_inter_link_info(_inter_type), get_intra_link_info(_intra_type));
                cur_speedup.push_back(baseline/fastall2all_r.t);
                delete[] mat;

            }
            double speedup_avg, speedup_sd;
            compute_average_standard_deviation(&cur_speedup, &speedup_avg, &speedup_sd);
            struct statistics_t speedup_r = {.avg = speedup_avg, .err = speedup_sd / sqrt(test_times)};
            algo_speedup.insert(make_pair(cur_config, speedup_r));

            double time_avg, time_sd;
            compute_average_standard_deviation(&cur_time, &time_avg, &time_sd);
            struct statistics_t time_r = {.avg = time_avg, .err = time_sd / sqrt(test_times)};
            algo_time.insert(make_pair(cur_config, time_r));
        }
    }

    // 把结果写到文本文件（列格式与 plot/ 下的 Python 脚本约定一致），
    // 同时打印到终端方便直接观察。
    // output benchmark results to files
    string benchmark_dir(BENCHMARK_DIR);

    ofstream speedup_f;
    speedup_f.open(benchmark_dir + "speedup_server_number_skewness.txt");
    cout << " ----------------- SPEEDUP -----------------" << endl;
    for (auto sr = algo_speedup.begin(); sr != algo_speedup.end(); sr++){
        speedup_f << sr->first.svr_n << " " << sr->first.s << " " << sr->second.avg << " " << sr->second.err << endl;
        cout << sr->first.svr_n << " " << sr->first.s << " " << sr->second.avg << " " << sr->second.err << endl;
    }
    speedup_f.close();

    ofstream time_f;
    time_f.open(benchmark_dir + "time_server_number_skewness.txt");
    cout << " ----------------- TIME COST -----------------" << endl;
    for (auto tr = algo_time.begin(); tr != algo_time.end(); tr++){
        time_f << tr->first.svr_n << " " << tr->first.s << " " << tr->second.avg << " " << tr->second.err << endl;
        cout << tr->first.svr_n << " " << tr->first.s << " " << tr->second.avg << " " << tr->second.err << endl;
    }
    time_f.close();
}


// 扫描单条消息大小，并对比 scaling 开/关的效果。
// 矩阵元素取值 0..MAX_ELEMENT-1；第 k 个配置把"最大元素"对应的消息大小定为
// transfer_sizes[k] MB，于是 MBpu = transfer_sizes[k] / MAX_ELEMENT。
// （随机元素的平均值约为 transfer_sizes[k]/2，不是 transfer_sizes[k]。）
//   - 消息很小时，固定延迟 alpha 占主导，scaling 减少阶段数可能更有利；
//   - 消息很大时，传输时间 beta 占主导，保持高精度（不缩放）通常更合适。
// fstr 用于区分不同 scaling 策略的输出文件名（"0"/"1"/"2"）。
void FastAll2AllTester::transfer_size_benchmark(bool enable_scaling, INTRA_LINK_TYPE _intra_type, INTER_LINK_TYPE _inter_type, scaling_success_condition fn, string fstr){
    cout << "Benchmarking the effect of transfer size, enable scaling: " << enable_scaling <<endl;
    map<struct server_transfer_config_t, struct statistics_t> algo_speedup;
    map<struct server_transfer_config_t, struct statistics_t> algo_time;

    uint g_n = 16;  // fix the number of GPU

    vector <double> transfer_sizes = {0.0001, 0.001, 0.01, 0.05, 0.1, 1, 10, 100, 1000};    // unit MB
    // vector <double> transfer_sizes = {0.001, 0.01, 0.1, 1};    // unit MB

    for (uint s_n = 64; s_n >=16; s_n -=16){
        for (auto t_sz = transfer_sizes.begin(); t_sz != transfer_sizes.end(); t_sz ++){
            vector <double> cur_speedup;
            vector <double> cur_time;
            struct server_transfer_config_t cur_config = {.svr_n=s_n, .sz=*t_sz};
            // 令最大元素（MAX_ELEMENT 个单位）对应 *t_sz MB：
            // 一个单位 = (*t_sz / MAX_ELEMENT) MB，传进 pipeline 里参与 beta 计算。
            double MBpu = (*t_sz) / MAX_ELEMENT;

            // 原实现每个配置只采样 1 次（注意这里是 tt < 1，而不是 test_times），
            // 因此下面算出的标准差恒为 0，err 只有形式意义。
            for (uint tt = 0; tt < 1; tt++){
                uint dim = s_n * g_n;
                uint* mat = new uint[dim * dim];
                for (uint i = 0; i < dim; i++){
                    for (uint j = 0; j < dim; j++){
                        mat[i * dim + j] = rand() % MAX_ELEMENT;   // unit is 1 byte, change the size of each node
                    }
                }
                for (uint i = 0; i < dim; i++){
                    mat[i * dim + i] = 0;                 //clean the diagnal
                }

                // FastAll2All
                vector<LocalScheduler*> local_schedulers;
                for (uint s = 0; s < s_n; s++){
                    LocalScheduler* ls = new LocalScheduler(mat + s * dim * g_n, g_n, s_n, s, _intra_type);
                    local_schedulers.push_back(ls);
                }

                GlobalScheduler global_scheduler(s_n, g_n, local_schedulers, _inter_type, _intra_type, enable_scaling, MBpu);

                auto t1 = high_resolution_clock::now();   
                struct pipeline_result_t fastall2all_r = global_scheduler.pipeline2(MBpu, fn);
                auto t2 = high_resolution_clock::now();     
                auto duration = duration_cast<microseconds>(t2 - t1).count();
                cur_time.push_back(duration);


                for (auto ls = local_schedulers.begin(); ls != local_schedulers.end(); ls++){
                    delete (*ls);
                }

                // Baseline - Spreadout
                double baseline = spread_out_baseline(mat, s_n, g_n, get_inter_link_info(_inter_type), get_intra_link_info(_intra_type), MBpu);
                cur_speedup.push_back(baseline/fastall2all_r.t);
                delete[] mat;
                // cout << "pipeline time: " << fastall2all_r.t << " baseline: " << baseline<<endl;

            }
            double speedup_avg, speedup_sd;
            compute_average_standard_deviation(&cur_speedup, &speedup_avg, &speedup_sd);
            struct statistics_t speedup_r = {.avg = speedup_avg, .err = speedup_sd / sqrt(test_times)};
            algo_speedup.insert(make_pair(cur_config, speedup_r));

            double time_avg, time_sd;
            compute_average_standard_deviation(&cur_time, &time_avg, &time_sd);
            struct statistics_t time_r = {.avg = time_avg, .err = time_sd / sqrt(test_times)};
            algo_time.insert(make_pair(cur_config, time_r));
        }
    }

    // 把结果写到文本文件（列格式与 plot/ 下的 Python 脚本约定一致），
    // 同时打印到终端方便直接观察。
    // output benchmark results to files
    string benchmark_dir(BENCHMARK_DIR);

    ofstream speedup_f;
    speedup_f.open(benchmark_dir + "speedup_server_number_transfer" + fstr + ".txt");
    cout << " ----------------- SPEEDUP -----------------" << endl;
    for (auto sr = algo_speedup.begin(); sr != algo_speedup.end(); sr++){
        speedup_f << sr->first.svr_n << " " << sr->first.sz << " " << sr->second.avg << " " << sr->second.err << endl;
        cout << sr->first.svr_n << " " << sr->first.sz << " " << sr->second.avg << " " << sr->second.err << endl;
    }
    speedup_f.close();

    ofstream time_f;
    time_f.open(benchmark_dir + "time_server_number_transfer" + fstr + ".txt");
    cout << " ----------------- TIME COST -----------------" << endl;
    for (auto tr = algo_time.begin(); tr != algo_time.end(); tr++){
        time_f << tr->first.svr_n << " " << tr->first.sz << " " << tr->second.avg << " " << tr->second.err << endl;
        cout << tr->first.svr_n << " " << tr->first.sz << " " << tr->second.avg << " " << tr->second.err << endl;
    }
    time_f.close();

}

// 用牛顿迭代求 Zipf 分布的逆 CDF：给定概率 p 和取值范围 N，
// 返回对应的流量大小（1..N）。
// 公式来自 Castan 的 pcap 生成脚本；这里只关心"能生成近似 Zipf 的随机流量"，
// 精度由 tolerance 控制（0.01）。
uint32_t zipf_distribution::zipf_inverse_cdf_fast(double p, uint32_t N){
    if (p > 1.0 || p < 0){
        printf("ERROR: probability must be within [0,1]\n");
        return 0.0;
    }

    double tolerance = 0.01;
    double x = (double) N / 2.0;   // 牛顿迭代初值：取范围中点

    double D = p * (12 * (pow(N, 1 - s) - 1) / (1 - s) +
                    6 - 6 * pow(N, -s) +
                    s - pow(N, -1 - s) * s);
    while (1){
        double m = pow(x, -2 - s);
        double mx = m * x;
        double mxx = mx * x;
        double mxxx = mxx * x;

        double a = 12 * (mxxx - 1) / (1 - s) + 6 * (1 - mxx) + (s - (mx * s)) - D;
        double b = 12 * mxx + 6 * (s * mx) + (m * s * (s + 1));
        double newx = MAX(1, x - a / b);
        if (fabs(newx - x) <= tolerance)
            return uint32_t(newx);
        x = newx;
    }
}


// 生成 N 个服从 Zipf 分布的流量值：每次取 [0,1] 均匀随机数 p，
// 通过逆 CDF 映射成 1..MAX_ELEMENT 的流量大小。
void zipf_distribution::zipf(vector<uint> * r, uint N){
    double p = 0.0;
    uint sampled_transfer_size = 0;
    for (uint i = 0; i < N; i ++){
        p = (double) rand() / (double) RAND_MAX;
        sampled_transfer_size = zipf_inverse_cdf_fast(p, MAX_ELEMENT);
        r->push_back(sampled_transfer_size);
    }
}