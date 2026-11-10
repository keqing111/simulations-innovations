#include <cstring>
#include "global.h"
#include "define.h"
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <cmath>
#include "../topology/model.h"
using namespace chrono;
using namespace std;

// ============================================================================
// GlobalScheduler 的实现：把 server 级分解与 server 内调度拼成一条流水线，
// 再用 alpha-beta 模型逐阶段累加时间。
//
// 计时模型的核心思想（pipeline2）：
//   - server 间传输和 server 内搬运可以重叠（流水线）；
//   - 启动时将同一源 server 对所有目的 server 的 balance 时间串行相加，
//     再取各源 server 最大值；每个置换中一源只对应一目的，dispatch 直接取最大值；
//   - server 间传输在所有 server 之间并行，单个阶段的时间是
//     alpha + freq*beta*MBpu；
//   - 每个流水阶段取 max(server 内耗时, server 间耗时)，因为两者可以重叠；
//     第一段之前还有一次性的 balance，最后一段之后还有收尾 dispatch。
// ============================================================================

// 构造函数：把局部调度器汇总成 server 级矩阵。
// 对每个 (源 server, 目的 server)：
//   - prepare_load_balance 已经算出每 GPU 平均发送量；
//   - check_balance_dispatch 判断是否需要平衡；若不需要（if_approx=true），
//     用"最忙 GPU"的发送量作为近似目标，避免做 balance；
//   - 最终 data[src][dst] 写入"每 GPU 代表发送量"，供 FastAll2All 做 server 级分解。
// 注意它不是 server 间的总流量：平衡模式下总流量约等于该值乘 gpu_n，
// 近似模式下它只是最忙 GPU 的值，不能直接乘 gpu_n。
// 阅读约定：N=server_n，G=gpu_n；元素的单位是“数据单元”，不是字节。
// 一个单元代表 MBpu MB；这里的 MB 是十进制 MB（1e6 字节）。
GlobalScheduler::GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, INTER_LINK_TYPE type, INTRA_LINK_TYPE itype, bool scaling, double MBpu){
    server_n = _server_n;
    gpu_n = _gpu_n;
    locals = _locals;       // localschedulers
    enable_scaling = scaling;
    inter_type = type;
    inter_info = get_inter_link_info(type);
    intra_type = itype;
    intra_info = get_intra_link_info(itype);
    if_approx = new bool[locals.size() * server_n];     // 这是创建了一个 bool 数组，用于什么呢？locals.size() 从 test 中就是 server_n 的大小
    // 答：对，每台 server 一个 LocalScheduler，正常输入 locals.size()==N。
    // 数组有 N*N 项，每项对应一个“源 server -> 目的 server”，不是每 GPU 一个标志。
    // if_approx[src*N+dst]==true 表示这对 server 不做机内负载均衡，改用最大 GPU -> 为什么会有这对 server 不做 balance 的情况？
    // 待发量代表其 server 级需求；false 表示之后需要做 balance。
    memset(if_approx, false, sizeof(bool) * locals.size() * server_n);  // 内存初始化，为什么在这里？这和计算 cpu 调度速度生成有关吧？
    // 答：new bool[] 本身不保证元素为 false，memset 是把刚分配的状态表置为“默认不跳过”。
    // 下面又逐项写入 check_balance_dispatch 的判断结果；正常完整输入下会覆盖全部项。
    // 它是规划状态的初始化，不是设置通信时钟，也不是为了模拟 CPU 波动。
    // 若外层计时覆盖整个构造函数，它当然计入构造开销；pipeline2 内的 planning_us
    // 从进入 pipeline2 才开始，因此不包含这里的分配、清零和需求汇总。

    // contruct the matrix from the local scheduler result
    uint *data = new uint[server_n * server_n];
    for (auto local = locals.begin(); local != locals.end(); local++){
       (*local) -> prepare_load_balance();
       // local.cpp：统计各源 GPU 发往每个远端 server 的总量 row_sum，取
       // ceil(总量/G) 存入 server2server_data；同机流量另存 intrinsic_all2all，
       // 并把 server 级对角项设为 0。这里只统计，尚未真的生成 balance 搬运计划。
       uint src_svr = (*local) -> get_server_id();      // 什么定义？得到这个 local scheduler 对应的 server id
       // 答：是的。local.h 的内联 getter 直接返回成员 server_id；test.cpp 创建
       // LocalScheduler(..., s, ...) 时把源 server 编号 s 传进去，编号为 0..N-1。
       for (uint j = 0; j < server_n; j++){
            if_approx[src_svr * server_n + j] = (*local) -> check_balance_dispatch(j, MBpu);
            // local.cpp：用当前需求估算机内 beta_cost/alpha_cost，若 <2 返回 true。
            // 这是“固定启动开销较显著时跳过 balance”的原生启发式，
            // 不是精确比较两套完整调度，也没有查询跨 DC 路径/共享瓶颈。
            if (if_approx[src_svr * server_n + j]){
                // 决定不 balance：用最忙 GPU 的量作为该目的 server 的代表值，决定不 balance 是什么意思？
                // 答：不把源机各 GPU 发往 j 的负载搬匀，保留原来分布；并不取消发送，
                // 也不跳过后面的 dispatch。例如各 GPU 待发量 [2,10]，平衡目标是 6，
                // 跳过时 server2server_approx 改用 10 作为每 GPU 调度容量上界。
                // 真正的待发数据仍只有 12，不会复制成 20；多出的容量可以空着，
                // 但默认 permutation_set_cost 仍按该阶段的名义 freq 估算时间。
                (*local) -> server2server_approx(j);
            }
            data[src_svr * server_n + j] = (*local)->server2server_data[j];
       }
    }

    mat.copy(data, server_n);
    // matrix.cpp 的 copy 是深拷贝，data 按 data[src*N+dst] 行优先存储；
    // 所以下面可释放临时 data，不会使成员 mat 悬空。
    // 调用约定：locals 按 server_id 排列且每台出现一次；pipeline2 后面会用 lid
    // 直接索引 if_approx，hooks 还会用 locals[s]，不能任意打乱这个 vector。
    // cout << "Global scheduler prints server2server matrix: " << endl;
    // mat.print();
    delete[] data;
}

// 同上，但直接接收自定义的链路参数（用于扫描带宽/拓扑的实验）。
// 阅读提醒：此重载只赋 inter_info/intra_info，没有赋 inter_type/intra_type。
// 而 pipeline2/pipeline3 构造 FastAll2All 时仍读取这两个枚举成员；不能据此
// 假定自定义 alpha/beta 已完整传入内部 scaling。这里仅说明现状，不修改实现。
GlobalScheduler::GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, struct link_info_t _inter_link, struct link_info_t _intra_link, bool scaling, double MBpu){
    server_n = _server_n;
    gpu_n = _gpu_n;
    locals = _locals;
    enable_scaling = scaling;
    inter_info = _inter_link;
    intra_info = _intra_link;
    if_approx = new bool[locals.size() * server_n];
    memset(if_approx, false, sizeof(bool) * locals.size() * server_n);
    // contruct the matrix from the local scheduler result
    uint *data = new uint[server_n * server_n];
    for (auto local = locals.begin(); local != locals.end(); local++){
       (*local) -> prepare_load_balance();
       uint src_svr = (*local) -> get_server_id();
       for (uint j = 0; j < server_n; j++){
            if_approx[src_svr * server_n + j] = (*local) -> check_balance_dispatch(j, MBpu);
            if (if_approx[src_svr * server_n + j]){
                (*local) -> server2server_approx(j);
            }
            data[src_svr * server_n + j] = (*local)->server2server_data[j];
       }
    }
    mat.copy(data, server_n);
    // cout << "Global scheduler prints server2server matrix: " << endl;
    // mat.print();
    delete[] data;
}


GlobalScheduler::~GlobalScheduler(){
    delete[] if_approx;
}

void GlobalScheduler::set_schedule(ScheduleMode mode,const native_topology::Topology* topology) {
    if(decomposition_configured_&&mode!=ScheduleMode::Native)
        throw invalid_argument("complete decomposition conflicts with topology matching override");
    if((mode!=ScheduleMode::Native&&mode!=ScheduleMode::BandwidthAware&&mode!=ScheduleMode::CompletionTimeAware)
       ||(!topology&&mode!=ScheduleMode::Native))throw invalid_argument("schedule requires a topology and valid mode");
    if(topology) {
        if(topology->n!=server_n||topology->g!=gpu_n||locals.size()!=server_n)
            throw invalid_argument("topology/scheduler dimensions");
        for(uint s=0;s<server_n;++s)if(!locals[s]||locals[s]->get_server_id()!=s)
            throw invalid_argument("locals must be ordered by server id");
    }
    schedule_mode_=mode;topology_=topology;
}
// 显式选择一种完整 BvN 匹配策略；只影响 pipeline2 中“补齐 + 分解”这一段，
// 后续的 Local balance、逐阶段 dispatch、重叠递推和 Native 拓扑计价都不变。
// 约束：
//   - 未调用本函数时，pipeline2 仍走原生 decompose()，行为与旧路径一致。
//   - 配置后只允许 Native 调度模式；BandwidthAware/CompletionTimeAware 会用
//     adjust_matching() 改写每轮匹配，与本策略“自己选匹配”冲突。
//   - 仍可用 set_schedule(Native, &topology) 让后续阶段按真实资源计价，但四种
//     策略本身不读取拓扑奖励，只在补齐后的整数残量上选择匹配。
//   - 与 prepare/communication/defer_decomposition 等覆盖型 hooks 互斥；
//     纯观测 hooks 仍可用。pipeline()/pipeline3() 不接入本配置。
void GlobalScheduler::set_decomposition(const DecompositionOptions &options) {
    // 与 FastAll2All::decompose 的入口校验保持一致；放在 setter 里是为了让
    // 配置错误在流水线开始前就失败，而不是等到局部需求已被修改后才抛出。
    if (options.strategy != DecompositionStrategy::Kuhn &&
        options.strategy != DecompositionStrategy::MaximumBottleneck &&
        options.strategy != DecompositionStrategy::MaximumElimination &&
        options.strategy != DecompositionStrategy::ResidualDegree)
        throw invalid_argument("unsupported decomposition strategy");
    if (!options.threshold_budget)
        throw invalid_argument("threshold budget must be positive");
    if (schedule_mode_ != ScheduleMode::Native)
        throw invalid_argument("complete decomposition conflicts with topology matching override");
    decomposition_options_ = options;
    decomposition_configured_ = true;
    decomposition_stats_ = DecompositionStats();
}
vector<uint64_t> GlobalScheduler::collect_pending_units()const {
    vector<uint64_t> pending(server_n*server_n*gpu_n);
    for(uint s=0;s<server_n;++s)for(uint d=0;d<server_n;++d)for(uint u=0;u<gpu_n;++u)
        // topology阶段的self-loop仅是补齐容量；intrinsic只由机内路径消费一次。
        pending[(s*server_n+d)*gpu_n+u]=topology_&&s==d?0:locals[s]->pending_units(d,u);
    return pending;
}
native_topology::GpuStageCost GlobalScheduler::topology_stage_cost(const PermutationSet& stage,
        const vector<uint64_t>& actual,double MBpu)const {
    if(actual.size()!=size_t(server_n)*gpu_n)throw invalid_argument("stage payload dimensions");
    vector<native_topology::GpuTransfer> flows;flows.reserve(actual.size());
    for(uint s=0;s<server_n;++s) {
        uint d=stage.mp.at(s);
        if(d>=server_n)throw invalid_argument("stage destination");
        if(s!=d)for(uint u=0;u<gpu_n;++u)if(actual[s*gpu_n+u])
            flows.push_back({s*gpu_n+u,d*gpu_n+u,double(actual[s*gpu_n+u])*MBpu*1e6});
    }
    return topology_->evaluate_gpu(flows);
}
// pipeline3：server 内调度结构与 pipeline2 相同（只 balance 一次 + 每阶段 dispatch），
// 区别是 server 内耗时由调用者传入的 fn 计算，而不是固定用 spread_out。
// 这样可以在 topology_benchmark 里评估全互联、双环等不同拓扑对 FAST 的影响。
// 注意：pipeline3 不做 scaling（即使构造时 enable_scaling=true），也不支持 hooks。
struct pipeline_result_t GlobalScheduler::pipeline3(double MBpu, intra_transfer_topo_fn fn){
    if(decomposition_configured_)throw logic_error("complete decomposition is supported by pipeline2 only");
    if(topology_)throw logic_error("configured topology is supported by pipeline2 only");
    // 1) server 级分解：得到置换集合 p_sets
    FastAll2All all2all(&mat, gpu_n, inter_type, intra_type, MBpu);
    all2all.to_scaled_doubly_stochastic_matrix();
    all2all.decompose();

    // cout << "birkhoff decompostion succeed, pset_n:  " << all2all.p_sets.size() << " ,MBpu: " << MBpu <<endl;
    // //print first result
    // all2all.p_sets[0].print_permutation_matrix();
    // cout << "frequency: " << all2all.p_sets[0].get_freq() << endl;

    // B. 制定 server 内计划（OPTION 2）
    // 先在开头统一做一次 balance，把每个 GPU 的待发负载拉平；
    // 之后每个置换阶段只做 dispatch，不再重复平衡。
    // OPTION 2: load balance in the begining (i.e., convert to balanced workload), only dispatch at each step
    uint locals_sz = locals.size(), pset_sz = all2all.p_sets.size();
    uint pid = 0;
    uint lid = 0;

    // ds[pid][lid]：第 pid 个置换阶段、第 lid 个源 server 需要做的 server 内 dispatch
    vector <vector<struct load_balance_result>> ds(pset_sz, vector<struct load_balance_result>(locals_sz, {.balance=NULL, .dispatch=NULL}));
    // dispatch for each pset
    for (pid = 0; pid != pset_sz; pid++){
        for (lid = 0; lid != locals_sz; lid ++){
            ds[pid][lid].dispatch = new uint[gpu_n * gpu_n];
            memset(ds[pid][lid].dispatch, 0,  sizeof(uint) * gpu_n * gpu_n);
        }
    }

    vector <vector<struct load_balance_result>> bs(locals_sz, vector<struct load_balance_result>(server_n, {.balance=NULL, .dispatch=NULL}));

    // 分配 balance 结果矩阵：bs[源 server][目的 server] 是一个 gpu_n × gpu_n 的
    // "本 server 内 GPU 之间搬了多少数据"矩阵。
    // balance only once
    for (lid = 0; lid < locals_sz; lid++){
        for (uint s = 0; s < server_n; s++){
            bs[lid][s].balance = new uint[gpu_n * gpu_n];
            memset(bs[lid][s].balance, 0, sizeof(uint) * gpu_n * gpu_n);
        }
    }
    
    // 生成 server 内平衡计划：对每个目的 server s，若构造时判定"值得平衡"，
    // 就把本 server 各 GPU 发往 s 的负载调到平均值；if_approx 为 true 则跳过。
    // generate schedule for intra-server all2all - balance first
    for (lid = 0; lid < locals_sz; lid++){
        for (uint s = 0; s < locals_sz; s++){
           if (s == locals[lid]->get_server_id()){
            continue;
           }
           if (!if_approx[lid * server_n + s]){ // check the alpha and beta cost of balance and dispatch, do balance only if beta cost is dominant
                locals[lid]->server2server_balance(s, bs[lid][s]);
            }
            // else{
            //     cout << lid * server_n + s << endl;
            // }
        }
    }

    // 生成每个置换阶段的 server 内 dispatch 计划：
    // 置换集合 mp 给出本阶段"源 server -> 目的 server"的对应关系；
    // 对每个源 GPU，从剩余数据中最多提取 freq 的量，并记录到达目的机后
    // 需要的 GPU->GPU 分发（ds[阶段][源 server]，存放索引不等于执行位置）。
    // generate schedule for intra-server all2all - dispatch for each step
    pid = 0;
    for (auto pset = all2all.p_sets.begin(); pset != all2all.p_sets.end(); pset++){
        lid = 0;
        for (auto local = locals.begin(); local != locals.end(); local ++){
            uint src_svr = (*local) -> get_server_id();
            auto lookup = (*pset).mp.find(src_svr);
            if (lookup == (*pset).mp.end()){
                LOG("error decomposition result");
                exit(1);
            }
            uint dst_svr = lookup -> second;
            // cout << "pid: " << pid << " , lid: "<< lid << " , src svr: " << src_svr << " , dst svr: " << dst_svr << endl;
            (*local) -> server2server_dispatch(dst_svr, ds[pid][lid], (*pset).get_freq());
            lid ++;
        }
        pid++;
    }
    // cout << "frequency: " << all2all.p_sets[0].get_freq() << endl;
    // uint dst_svr = all2all.p_sets[0].mp.find(0) -> second;
    // print_matrix(ds[0][0].dispatch, gpu_n, gpu_n);
    // locals[0]->print(dst_svr);

    // cout << "complete intra-tile all2all schedule" << endl;
    // initial intra-all2all and balance
    uint prev_pid = 0, cur_pid = 1, next_pid = 2;
    double intra1, intra2, intra_time, largest_intra_time, inter, balance_time;

    // C. 用 alpha-beta 模型估算端到端时间。
    // t     = 实际累加的总时间；
    // bound = 所有 server 间传输时间之和，代表"server 内开销全部被掩盖"时的下界。
    // evaluate the algorithm runtime
    double t = 0.0, bound = 0.0;

    // balance
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        balance_time = 0;
        for (uint s = 0; s < locals_sz; s++){
            if (s == locals[i]->get_server_id()){
                continue;
            }
            double cur_spd = (*fn)(bs[i][s].balance, gpu_n, locals[i]->get_link_info(), MBpu);
            balance_time += cur_spd;
            // cout << "first balance: "<< endl;
            // print_matrix(bs[i][s].balance, gpu_n, gpu_n);


        }
        largest_intra_time = MAX(largest_intra_time, balance_time);
    }
    t += largest_intra_time;

    // cout << "balance time: " << t << endl;
    // first inter transfer
    inter = permutation_set_cost(all2all.p_sets[0].get_freq(), MBpu);
    // intrinsic all2all
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        intra1 = (*fn)(locals[i]->get_intrinsic_all2all(), gpu_n, locals[i]->get_link_info(), MBpu);
        largest_intra_time = MAX(largest_intra_time, intra1);
    }

    t += MAX(inter, largest_intra_time);
    bound += inter;


    // 流水线中段：每个阶段做两件事——
    //   1) 上一阶段的 server 内 dispatch（ds[prev_pid]）仍在进行；
    //   2) 当前阶段的 server 间传输（p_sets[cur_pid]）同时进行。
    // 两者可以重叠，所以本阶段耗时 = max(server 内耗时, server 间耗时)。
    // pipeline the middle psets: inter transfer and dispatch
    for (cur_pid = 1, prev_pid = cur_pid - 1;
    cur_pid < pset_sz;
    prev_pid = cur_pid, cur_pid++){

        largest_intra_time = 0;
        for (uint i = 0; i < locals_sz; i++){
            // previous step's intra-all2all dispatch
            if (cur_pid != 0){
                intra1 = (*fn)(ds[prev_pid][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
            }
            largest_intra_time = MAX(largest_intra_time, intra1);
        }

        // cout << cur_pid << " - " << t << " , intra: " << largest_intra_time<< " , inter: " << inter <<  " ,inter bigger: " << ((inter > largest_intra_time) ? "yes" : "no") << endl;
        // current step inter-all2all inter-server transfer
        inter = permutation_set_cost(all2all.p_sets[cur_pid].get_freq(), MBpu);
        t += MAX(largest_intra_time, inter);
        bound += inter;
    }

    // 收尾：最后一段 server 内 dispatch 没有后续网络传输可以重叠，
    // 直接加到总时间上。
    // dispatch for the last pset
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        intra1 = (*fn)(ds[pset_sz-1][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
        largest_intra_time = MAX(largest_intra_time, intra1);
    }
    t += largest_intra_time;

    // cout << "FastAll2All time: "<< t << " ,bound: "<< bound << " ,extra overhead: " << (t - bound) / bound * 100 << " %"<<endl;

    // cout << "release intra-all2all memory allocation" << endl;
    // free memory for intra all2all
    for(pid = 0; pid < pset_sz; pid ++){
        for (lid = 0; lid < locals_sz; lid++){
            delete[] ds[pid][lid].dispatch;
        }
    }

    for (lid = 0; lid < locals_sz; lid++){
        for (uint s = 0; s < locals_sz; s++){
            delete[] bs[lid][s].balance;
        }
    }

    // cout << "complete release intra-all2all memory allocation" << endl;
    // ratio = (t - bound) / t：server 内开销中"没能被网络传输掩盖"的比例。
    // ratio 越小说明流水线隐藏得越好；为 0 表示 server 内工作完全被重叠。
    double ratio = (t - bound) / t;
    struct pipeline_result_t pipe_r = {.t = t, .ratio = ratio};
    return pipe_r;
}


// ============================================================================
// pipeline2：benchmark 默认使用的端到端流程。
//
// 先区分“在 CPU 上生成计划”和“用公式估算执行时间”：
// 本函数不会创建 ns-3 流、不会等待真实 GPU/NIC 完成通信，也不会真的 sleep。
// B 中的 balance/dispatch 调用是在内存中搬动/扣减需求并写计划矩阵；
// C 再把这些计划换算成预计通信时间。CPU 规划用时与返回的通信时间 t 是两回事。
//
// 主要数据与依赖（声明在 .h，具体算法在对应 .cpp）：
//   scheduler/local.{h,cpp} ：LocalScheduler，持有本源 server 的 G 行 GPU 需求；
//                            balance/dispatch 会消耗或重排其 balanced_data。
//   algorithm/matrix.{h,cpp}：Matrix，保存 N*N 的每 GPU 代表量并统计行/列和。
//   algorithm/all2all.{h,cpp}：FastAll2All，缩放、补齐及 Birkhoff 分解；
//                            PermutationSet.mp[src]=dst，get_freq() 为阶段容量。
//   scheduler/config.{h,cpp}：link_info_t 和 spread_out，负责 alpha-beta 计价；
//                            spread_out 返回一个时间，不会再做 Birkhoff 分解。
//   scheduler/global.h     ：pipeline_hooks_t，可选研究回调与统计输出。
//   define.h               ：MAX/MIN 宏、LOG 和 scaling 搜索范围等常量。
//
// 分四部分（不是只有四个通信 stage；置换 stage 数为 K=p_sets.size()）：
//   A. 分解：可选 scaling，然后双随机化 + Birkhoff 分解，得到 p_sets；
//   B. 制定 server 内计划：
//        - 对所有目的 server 做一次 balance（除非构造时判定可以跳过）；
//        - 对每个置换阶段提取实际发送量，记录到达目的机后所需的 dispatch；
//   C. 计时（alpha-beta 模型）：
//        - balance 单独启动；intrinsic all-to-all 与第一段跨机传输重叠；
//        - 中间每个阶段：本阶段 server 间传输 与 上一阶段的 server 内 dispatch 重叠；
//        - 最后补上最后一段 dispatch；
//   D. 返回总时间 t 和"未重叠开销比例" ratio。
//
// 计时流程，K=3 为例（Bk 为一次性 balance，H 为原本就只在机内传的需求）：
//   [Bk] -> [I0 || H] -> [I1 || D0] -> [I2 || D1] -> [D2]
//   Ik = 第 k 个置换的跨机时间；Dk = 该置换数据到达后的机内分发时间；
//   || 表示允许重叠，每一格取 max，两格之间把时间相加。
// 一般 K>=1 时：t = B + max(I0,H) + sum(k=1..K-1) max(Ik,D[k-1]) + D[K-1]。
// 例如全部以 us 计，B=2、H=3、I=[10,20,8]、D=[4,12,5]：
//   t=2+10+20+12+5=49，bound=10+20+8=38，ratio=11/49。
//   D1=12 比 I2=8 慢，因此露出 4us；balance 的 2us 和收尾的 5us 也无法隐藏。
// K=0 时没有跨机 stage，代码得到 t=B+H。
// 这是原生的按流水级取全局最大值的解析计时，不是独立 rank 的事件模拟。
// 它也不实现“某 rank 先完成就独自释放到下一 stage”；模型上各格串接，
// 但不能把这些 max 称为在实机/网络中额外插入了显式 barrier。
//
// hooks 是后加的研究接口；nullptr 为默认原生路径。非空时可只观测，也可通过
// prepare 改调度、communication 改跨机代价，不能一概把所有 hooks 称为纯插桩。
// 本函数会修改 locals 的剩余需求，不是可直接在同一组 locals 上反复调用的纯函数；
// 公平比较不同算法时需要从相同原始矩阵重新创建 LocalScheduler。
// ============================================================================
struct pipeline_result_t GlobalScheduler::pipeline2(double MBpu, scaling_success_condition fn, pipeline_hooks_t* hooks){
    if(decomposition_configured_&&hooks&&(hooks->defer_decomposition||hooks->prepare||hooks->communication))
        throw invalid_argument("complete decomposition conflicts with overriding hooks");
    if(topology_&&(!isfinite(MBpu)||MBpu<=0))throw invalid_argument("invalid data unit");
    if(topology_&&hooks&&(hooks->defer_decomposition||hooks->prepare||hooks->communication))
        throw invalid_argument("topology schedule conflicts with overriding hooks");
    // 第 0 步：记录规划起点。steady_clock 测本机经过的墙钟时间，不是通信时间，
    // 也不是进程实际占用 CPU 的时间；系统抢占等会影响它。只有 hooks 非空才输出。
    auto planning_start = steady_clock::now();
    decomposition_stats_=DecompositionStats();
    if (hooks) {
        hooks->decomposition_stats=DecompositionStats();
        hooks->native_decompose_us = 0;
        hooks->padding_wall_us = 0;
        hooks->planning_us = 0;
        if (hooks->defer_decomposition && !hooks->prepare)
            throw invalid_argument("deferred decomposition requires prepare callback");
        // defer=true 时 A 中跳过内置补齐/分解，后面必须由 prepare 填好 p_sets；
        // 因此缺少 prepare 是输入错误，不能继续使用一个未生成的调度。
    }
    // A. server 级分解：缩放（可选）-> 双随机化 -> Birkhoff 分解
    // 第 1 步：all2all.h / all2all.cpp 构造函数深拷贝 mat，初始化 N 行/N 列的二分匹配
    // 工作区，并根据 inter_type/intra_type 取得 scaling 判据所用的链路参数。
    // 这里尚未分解，p_sets 为空；不会把 GPU 级原始数据传给匹配器。
    FastAll2All all2all(&mat, gpu_n, inter_type, intra_type, MBpu);
    if (enable_scaling){
        all2all.to_scaled_matrix(fn);       // 看起来不像是原生的仿真代码，是后续添加上的创新？
        // 答：不是后加创新。上游原始提交 0712d29 的 pipeline2 就有这条调用，
        // 只是构造函数默认 scaling=false，关闭时直接跳过。
        // all2all.cpp：搜索缩放因子 q，用 S[i,j]=ceil(mat[i,j]/q) 做分解；
        // PermutationSet::get_freq() 会再乘 q，把阶段容量换回原始单位。
        // 这是量化/减少细碎阶段的原生折中，不是删除需求，也不是按 WAN 延迟加权。
        // fn 是函数指针，默认 balance_alpha_beta（all2all.cpp）：候选缩放后
        // 最大行/列和 <=N，或估计 alpha_cost/beta_cost<0.1，便接受该候选；
        // 二分按 define.h 规定的范围/步长搜索，失败则保留不缩放。
        // 真实 GPU payload 不随 q 放大/缩小；向上取整造成的多余阶段容量可为空。
    }
    if (!topology_ && (!hooks || !hooks->defer_decomposition)) {
        // 第 2 步：默认原生路径。计时仅覆盖下面“补齐+分解”，不含上面的 scaling。
        auto decomposition_start = steady_clock::now();
        all2all.to_scaled_doubly_stochastic_matrix();       // 这应该就是原生的 fast 分解矩阵的算法了
        if(hooks)hooks->padding_wall_us=duration<double,std::micro>(steady_clock::now()-decomposition_start).count();
        // 答：这是原生流程的“补齐前处理”，真正的 Birkhoff 分解是下一行 decompose。
        // matrix.cpp::get_sdsm_info 先求所有行和/列和的最大值 M；all2all.cpp
        // 再在行/列缺口处添加虚拟容量，使每行和、每列和都为 M。
        // 严格说这是“行列和均为 M 的非负矩阵”，除以 M 才是通常定义的双随机矩阵。
        // 补齐是为了保证正残余支持图上存在 perfect matching，不是生成真实业务流；
        // 可能补出 src==dst 的配对，它在物理含义上也不是新增的跨机通信；
        // 但原生 dispatch 对这种配对的处理并不特殊，边界问题见下方调用处。
        // 只有显式 set_decomposition() 时才替换分解器；否则仍调用无参数的原生
        // 入口。两条分支之后的 balance/dispatch/重叠递推完全相同。
        if(decomposition_configured_)all2all.decompose(decomposition_options_,&decomposition_stats_);
        else all2all.decompose();
        // all2all.cpp 调用链：decompose -> decompose_with(空回调) -> 循环：
        //   update_edges：仅保留残余值>0的行->列边（含补齐容量）；
        //   hungarian/hungarian_dfs：Kuhn 增广路匹配，给每个源 server 分配唯一目的
        //     server，每个目的本轮也只有一个源；不是带权最优匹配，不看 WAN 拓扑；
        //   update_permutation_sets：取匹配边的最小残量 w，所有匹配边减 w，
        //     记录 mp 和 frequency=w，直到累计 frequency 达到 M。
        // 输出 p_sets 是按该顺序执行的 K 个“置换+容量”阶段，不是原始 GPU 流列表。
        if (hooks) hooks->native_decompose_us = duration<double, std::micro>(steady_clock::now()-decomposition_start).count();
    }

    // cout << "birkhoff decompostion succeed, pset_n:  " << all2all.p_sets.size() << " ,MBpu: " << MBpu <<endl;
    // //print first result
    // all2all.p_sets[0].print_permutation_matrix();
    // cout << "frequency: " << all2all.p_sets[0].get_freq() << endl;

    // B. 制定 server 内计划（OPTION 2）
    // 先在开头统一做一次 balance，把每个 GPU 的待发负载拉平；
    // 之后每个置换阶段只做 dispatch，不再重复平衡。
    // OPTION 2: load balance in the begining (i.e., convert to balanced workload), only dispatch at each step
    // 这里的 balance 和 dispatch 分别指什么？
    // 答：二者都是机内 GPU 间的数据搬运，目的不同，不是两种跨机调度算法。
    // balance（local.cpp 的两参数重载）：在“源 server”内，把发往某目的 server
    // 的数据从重载 GPU 挪到轻载 GPU，目标为 ceil(总量/G)，最终目的 GPU 不变。
    // 整除时各 GPU 负载相等；不整除时允许少数 GPU 少发，不会凭空补真实数据。
    // dispatch（server2server_dispatch）：为一个 stage 从每个源 GPU i 的剩余需求中
    // 最多取 freq 个单元，按最终目的 GPU j 记录 i!=j 的机内分发量。
    // 物理含义：源机 GPU i -> 目的机 GPU i（跨机）-> 目的机 GPU j（dispatch）；
    // 所以 dispatch 在“目的机”发生，而不是跨机前先把源 GPU i 对齐到源 GPU j。
    // 这个解释依据 local.cpp 的原始英文说明“跨机先到同编号 GPU”以及 C 中
    // “上一 stage 的 dispatch 与下一次跨机重叠”的递推；相关头文件/实现中有些
    // 旧中文注释将其写成源端对齐，容易造成误解。这里的代码本身只存转运量。
    // 例：原来 s.GPU0 -> d.GPU1，balance 若未改变发送者，跨机先到 d.GPU0，
    // 再在 d 内做 GPU0 -> GPU1；若最终就是 d.GPU0，则不需要这次 dispatch。
    // 它按源机的需求生成/保存计划，但 C 会把它当作前一 stage 到达后的工作来计时。
    // balance/dispatch 的具体循环、取 min 与剩余量更新均在 scheduler/local.cpp。
    uint locals_sz = locals.size(), pset_sz = all2all.p_sets.size();
    uint pid = 0;
    uint lid = 0;

    // 第 3 步：准备一次性 balance 的记录。bs[lid][s].balance[a*G+b]
    // 表示为了发往 server s，源 server lid 的 GPU a 要搬给同机 GPU b 的量。
    // load_balance_result（local.h）有 balance/dispatch 两个指针；此处只用 balance。
    vector <vector<struct load_balance_result>> bs(locals_sz, vector<struct load_balance_result>(server_n, {.balance=NULL, .dispatch=NULL}));
    vector<uint> balance_storage(size_t(locals_sz)*server_n*gpu_n*gpu_n,0);

    // 分配 balance 结果矩阵：bs[源 server][目的 server] 是一个 gpu_n × gpu_n 的
    // "本 server 内 GPU 之间搬了多少数据"矩阵。
    // balance only once
    for (lid = 0; lid < locals_sz; lid++){
        for (uint s = 0; s < server_n; s++){
            bs[lid][s].balance = balance_storage.data()+(lid*server_n+s)*gpu_n*gpu_n;
        }
    }
    
    // 生成 server 内平衡计划：对每个目的 server s，若构造时判定"值得平衡"，
    // 就把本 server 各 GPU 发往 s 的负载调到平均值；if_approx 为 true 则跳过。
    // generate schedule for intra-server all2all - balance first
    for (lid = 0; lid < locals_sz; lid++){
        for (uint s = 0; s < locals_sz; s++){
           if (s == locals[lid]->get_server_id()){
            continue;
           }
           if (!if_approx[lid * server_n + s]){ // check the alpha and beta cost of balance and dispatch, do balance only if beta cost is dominant
                locals[lid]->server2server_balance(s, bs[lid][s]);
                // 两参数重载只做 balance，不提取本 stage 的数据。它会原地更新
                // balanced_data/row_sum，并向已清零的 bs 矩阵累计搬运量。
                // r 虽按值传入，内部的 uint* 仍指向此处的缓冲区，因此结果会写回来。
            }
            // else{
            //     cout << lid * server_n + s << endl;
            // }
        }
    }

    // 第 4 步（可选研究扩展，不是原始 FAST 必需步骤）：
    // 研究钩子：把 balance 之后每个 (源 server, 目的 server, 源 GPU) 的
    // 剩余 payload 拍平成 rows，交给 prepare 回调；回调可以重新决定分解/调度。
    // 之后 stages/payload_units 会记录最终采用的阶段和实际搬运量。
    // 正式策略在原生balance后接入；不需要调用者提供prepare回调。
    if(topology_) {
        auto decomposition_start=steady_clock::now();
        all2all.to_scaled_doubly_stochastic_matrix();
        if(hooks)hooks->padding_wall_us=duration<double,std::micro>(steady_clock::now()-decomposition_start).count();
        if(decomposition_configured_) {
            // 完整分解策略只替换“选匹配”；这里不调用
            // decompose(mode, topology, pending) 的拓扑匹配覆盖。topology_ 仍会
            // 在后续阶段计价中使用，因此这不是“忽略拓扑”，而是不把拓扑奖励
            // 混进四种 BvN 策略的匹配选择。
            all2all.decompose(decomposition_options_,&decomposition_stats_);
            if(hooks)hooks->native_decompose_us=duration<double,std::micro>(steady_clock::now()-decomposition_start).count();
        } else all2all.decompose(schedule_mode_,*topology_,collect_pending_units());
        pset_sz=all2all.p_sets.size();
    }
    if(hooks&&decomposition_configured_)hooks->decomposition_stats=decomposition_stats_;
    vector<vector<uint64_t>> topology_payload;
    if(topology_&&!hooks)topology_payload.assign(pset_sz,vector<uint64_t>(server_n*gpu_n));
    if (hooks) {
        hooks->dispatch_us.clear(); hooks->payload_units.clear();
        vector<uint64_t> rows=collect_pending_units();
        // local.cpp::pending_units(d,i) 对 balanced_data[i] 中目的 server d 的
        // G 列求和，返回当前真实待发量；不是补齐后的容量，也不改变任何需求。
        if (hooks->prepare) hooks->prepare(mat, rows, all2all);
        // prepare 的实现由调用者提供，可以重排/替换 p_sets；defer=true 时还必须
        // 负责生成分解。检查具体研究策略应看调用者，不能只看 pipeline2 得出结论。
        hooks->stages = all2all.p_sets;
        pset_sz = all2all.p_sets.size();
        // 回调可能改变 K，因此重新读取；payload_units[k][src*G+i] 后续记录
        // stage k 从源 server src 的 GPU i 真正取走多少，而非名义阶段容量。
        // communication 回调还应区分 src==dst，不能把同机数据也算作跨机字节。
        hooks->payload_units.assign(pset_sz, vector<uint64_t>(server_n * gpu_n));
    }
    // 第 5 步：为最终 K 个 stage 各分配 N 个 G*G dispatch 矩阵。
    // ds[k][src] 按源 server 存放，但其 GPU a->b 搬运实际发生在 mp[src] 目的机。
    vector <vector<struct load_balance_result>> ds(pset_sz, vector<struct load_balance_result>(locals_sz, {.balance=NULL, .dispatch=NULL}));
    vector<uint> dispatch_storage(size_t(pset_sz)*locals_sz*gpu_n*gpu_n,0);
    for (pid=0; pid<pset_sz; ++pid) for (lid=0; lid<locals_sz; ++lid) {
        ds[pid][lid].dispatch = dispatch_storage.data()+(pid*locals_sz+lid)*gpu_n*gpu_n;
    }

    // 生成每个置换阶段的 server 内 dispatch 计划：
    // 置换集合 mp 给出本阶段"源 server -> 目的 server"的对应关系；
    // 对每个源 server 的每块 GPU，从剩余数据中最多提取 freq 的量，
    // 记录到达目的机同编号 GPU 后还需做的 GPU->GPU 搬运（ds[阶段][源 server]）。
    // generate schedule for intra-server all2all - dispatch for each step
    pid = 0;
    for (auto pset = all2all.p_sets.begin(); pset != all2all.p_sets.end(); pset++){
        lid = 0;
        for (auto local = locals.begin(); local != locals.end(); local ++){
            uint src_svr = (*local) -> get_server_id();
            auto lookup = (*pset).mp.find(src_svr);
            // std::map::find 查询本 stage 的目的 server，不是在这里重新求匹配。
            // 完美匹配必须包含全部源 server；查不到说明分解/回调给出的计划不合法。
            if (lookup == (*pset).mp.end()){
                LOG("error decomposition result");
                exit(1);
            }
            uint dst_svr = lookup -> second;
            // cout << "pid: " << pid << " , lid: "<< lid << " , src svr: " << src_svr << " , dst svr: " << dst_svr << endl;
            // 仅修正显式topology入口；默认FAST/Local balance-dispatch语义保持不变。
            if (topology_ && dst_svr == src_svr) {
                ++lid;
                continue;
            }
            vector<uint64_t> before;
            if (hooks||topology_) for(uint i=0; i<gpu_n; ++i) before.push_back((*local)->pending_units(dst_svr,i));
            (*local) -> server2server_dispatch(dst_svr, ds[pid][lid], (*pset).get_freq());
            // get_freq（all2all.h）=frequency*scaling_factor，以原始数据单元返回。
            // local.cpp 按目的 GPU j 依次取 min(本行尚可发送的容量, 当前真实需求)，
            // 扣减 balanced_data 和 row_sum，i!=j 时累计 dispatch[i*G+j]。
            // 对 src!=dst，数据不足 freq 时只取真实数据；空余容量不会变成 payload。
            // 本轮正好等于或少于 freq，不意味着每块 GPU 都真发了 freq。
            // 默认非topology实现边界：若虚拟补齐产生 dst_svr==src_svr，这里仍调用同一个 helper；
            // helper 没有跳过同机块，而 prepare_load_balance 把该块复制到
            // intrinsic_all2all 后并没有从 balanced_data 清掉。因此不能保证这种
            // 槽位不再取到同机数据、生成额外 dispatch；其 row_sum 也未按同机块初始化。
            // 这是当前代码的特殊情况，不应把“补齐是虚拟容量”解释成已做了自环过滤。
            // 显式topology入口已在上方过滤；此处保留默认原生流程及历史结果语义。
            if (hooks) for(uint i=0; i<gpu_n; ++i)
                hooks->payload_units[pid][src_svr * gpu_n + i] = before[i] - (*local)->pending_units(dst_svr,i);
            if(topology_&&!hooks)for(uint i=0;i<gpu_n;++i)
                topology_payload[pid][src_svr*gpu_n+i]=before[i]-(*local)->pending_units(dst_svr,i);
            lid ++;
        }
        pid++;
    }
    if (hooks) hooks->planning_us = duration<double, std::micro>(steady_clock::now()-planning_start).count();
    // 规划计时在这里结束：包括本函数中的 scaling/分解、balance/dispatch 计划生成、
    // prepare 及 hooks 记录开销；不含 Global/Local 构造和下面 C 的代价计算、释放内存。
    // native_decompose_us 只是其中内置“补齐+分解”的局部时间，defer=true 时保持 0；
    // 不能把两个时间相加，也不能把 hooks 的计时说成无插桩的原生规划成本。
    // cout << "frequency: " << all2all.p_sets[0].get_freq() << endl;
    // uint dst_svr = all2all.p_sets[0].mp.find(0) -> second;
    // print_matrix(ds[0][0].dispatch, gpu_n, gpu_n);
    // locals[0]->print(dst_svr);

    // cout << "complete intra-tile all2all schedule" << endl;
    // initial intra-all2all and balance
    uint prev_pid = 0, cur_pid = 1, next_pid = 2;
    double intra1, intra2, intra_time, largest_intra_time, inter, balance_time;
    // prev_pid/cur_pid 用于后面的相邻 stage 递推；next_pid、intra2、intra_time
    // 在 pipeline2 中并未使用，是旧版本遗留声明，不代表还会算一次额外阶段。

    // 第 6 步：C. 用 alpha-beta 模型估算端到端时间，以下各时间单位都是 us。
    // t 是预计通信完成时间（不是上面测到的 CPU 规划用时），不会自动加 planning_us。
    // bound=sum Ik 是“固定当前调度，只忽略机内开销”的时间下界；
    // 不是全体算法在物理网络中的理论最优下界，改调度/改网络模型后不能直接共用它。
    // evaluate the algorithm runtime
    double t = 0.0, bound = 0.0;

    // 第 6.1 步 / 启动阶段 1：一次性 balance。
    // 同一个 server 的 server 内链路是共享的，所以把该 server 对所有
    // 目的 server 的 balance 时间相加；不同 server 之间并行，取最大值。
    // balance
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        balance_time = 0;
        for (uint s = 0; s < locals_sz; s++){
            if (s == locals[i]->get_server_id()){
                continue;
            }
            double cur_spd = spread_out(bs[i][s].balance, gpu_n, locals[i]->get_link_info(), MBpu);
            // config.cpp::spread_out(X,G,link,MBpu) 的完整计价规则：
            //   r=0..G-1，每轮 GPU a -> GPU (a+r)%G；
            //   m_r=max_a X[a,(a+r)%G]；m_r>0 才收 alpha+m_r*beta*MBpu；
            //   将各非空轮相加，返回 us。全零矩阵费用为 0，而不是仍收一次 alpha。
            // local.h::get_link_info() 只返回该机保存的 intra_info，不会测链路。
            // 这里是“用 spread-out 给机内搬运计价”，并不是又用全局 SpreadOut
            // 替换了 FAST 的跨机计划；FAST 的跨机顺序仍由 p_sets 决定。
            // 此函数不模拟交换机、物理路径和拥塞，只假定一轮内各对 GPU 可并发。
            balance_time += cur_spd;
            // cout << "first balance: "<< endl;
            // print_matrix(bs[i][s].balance, gpu_n, gpu_n);


        }
        largest_intra_time = MAX(largest_intra_time, balance_time);
    }
    t += largest_intra_time;

    // cout << "balance time: " << t << endl;
    if (hooks) hooks->balance_us = largest_intra_time;
    // 第 6.2 步 / 启动阶段 2：第一段 server 间传输，同时可以和"本 server 内部 all-to-all"
    // （intrinsic_all2all，即源/目的在同一 server 的流量）重叠，因此取两者最大值。
    // 空置换集合（没有跨 server 需求）时 inter 直接为 0。
    // communication 是本阶段 server 间代价的来源：默认 alpha + freq*beta*MBpu，
    // hooks 可以用自定义模型覆盖。
    // first inter transfer; empty inter-server demand needs no permutation.
    // [&] lambda 捕获当前局部状态：给定 stage 序号 k 返回 Ik。
    // 有 communication 回调则传“置换+实际取走量”给它，否则走文件末尾的原生公式。
    // 光有 hooks 并不会启用拓扑感知，必须由具体 communication 实现计入共享链路。
    auto communication = [&](uint k) {
        if(topology_)return topology_stage_cost(all2all.p_sets[k],
            hooks?hooks->payload_units[k]:topology_payload[k],MBpu).total_us;
        return hooks && hooks->communication ? hooks->communication(all2all.p_sets[k], hooks->payload_units[k])
            : permutation_set_cost(all2all.p_sets[k].get_freq(), MBpu);
    };
    inter = pset_sz ? communication(0) : 0;
    // intrinsic all2all
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        intra1 = spread_out(locals[i]->get_intrinsic_all2all(), gpu_n, locals[i]->get_link_info(), MBpu);
        // get_intrinsic_all2all（local.h）返回 G*G 的原始同机需求指针，
        // 与 balance/dispatch 为了转运远端流量而新增的机内搬运不是同一件事。
        largest_intra_time = MAX(largest_intra_time, intra1);
    }

    if (hooks) hooks->intrinsic_us = largest_intra_time;
    // t += max(I0,H)：第一段跨机与 intrinsic 同时开始，等较慢者结束再累加下一格。
    // pset_sz==0 时 I0=0，不访问 p_sets[0]，也不再执行中段/收尾 dispatch。
    t += MAX(inter, largest_intra_time);
    bound += inter;


    // 第 6.3 步 / 流水线中段：每个阶段做两件事——
    //   1) 上一阶段的 server 内 dispatch（ds[prev_pid]）仍在进行；
    //   2) 当前阶段的 server 间传输（p_sets[cur_pid]）同时进行。
    // 两者可以重叠，所以本阶段耗时 = max(server 内耗时, server 间耗时)。
    // pipeline the middle psets: inter transfer and dispatch
    for (cur_pid = 1, prev_pid = cur_pid - 1;
    cur_pid < pset_sz;
    prev_pid = cur_pid, cur_pid++){

        largest_intra_time = 0;
        for (uint i = 0; i < locals_sz; i++){
            // previous step's intra-all2all dispatch
            if (cur_pid != 0){
                // 本循环从 cur_pid=1 开始，因此这个判断始终成立；intra1 每次都会赋值。
                intra1 = spread_out(ds[prev_pid][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
                // i 是保存计划的源 server；实际 dispatch 在上一置换 mp[i] 的目的机。
                // 完美匹配下每个目的机只收到一个源，因此同构机内链路时“按源取 max”
                // 等价于“按目的机取 max”。注意实现仍取 locals[i] 的链路参数，
                // 若各目的机机内带宽不同，这里不能自动得到正确的目的机代价。
            }
            largest_intra_time = MAX(largest_intra_time, intra1);
        }

        // cout << cur_pid << " - " << t << " , intra: " << largest_intra_time<< " , inter: " << inter <<  " ,inter bigger: " << ((inter > largest_intra_time) ? "yes" : "no") << endl;
        // current step inter-all2all inter-server transfer
        inter = communication(cur_pid);
        if (hooks) hooks->dispatch_us.push_back(largest_intra_time);
        // cur_pid=k、prev_pid=k-1：本格时长 max(D[k-1],Ik)，不是 Dk+Ik。
        // 若 D[k-1]>Ik，本格会拖慢；若 <=Ik，该段 dispatch 被跨机时间完全隐藏。
        // Ik 很大不意味着总时长等于 sum Ik：开头 balance、末尾 dispatch 仍可能暴露。
        t += MAX(largest_intra_time, inter);
        bound += inter;
    }

    // 第 6.4 步 / 收尾：最后一段 server 内 dispatch 没有后续网络传输可以重叠，
    // 直接加到总时间上。
    // dispatch for the last pset
    largest_intra_time = 0;
    for (uint i = 0; pset_sz && i < locals_sz; i++){
        intra1 = spread_out(ds[pset_sz-1][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
        largest_intra_time = MAX(largest_intra_time, intra1);
    }
    t += largest_intra_time;
    if (hooks && pset_sz) hooks->dispatch_us.push_back(largest_intra_time);
    // 非空调度下 dispatch_us 恰有 K 项，依次为 D0..D[K-1]；中段写前 K-1 项，
    // 此处补最后一项。它们是模型预计 us，不是执行 dispatch helper 的 CPU 墙钟用时。

    // cout << "FastAll2All time: "<< t << " ,bound: "<< bound << " ,extra overhead: " << (t - bound) / bound * 100 << " %"<<endl;

    // 第 7 步：balance_storage/dispatch_storage 自动释放全部计划缓冲区，异常退出也安全。
    // 各 vector 容器和 all2all 的生命周期到 return 后自动结束；locals 仍归调用者拥有，
    // 此处既不 delete LocalScheduler，也不会恢复它已被 balance/dispatch 修改的需求。
    // cout << "release intra-all2all memory allocation" << endl;
    // cout << "complete release intra-all2all memory allocation" << endl;
    // 第 8 步：ratio=(t-bound)/t，是相对“当前调度纯跨机时间”额外暴露的时间，
    // 占总完成时间 t 的比例，不是“机内总工作量中未隐藏的百分比”，更不是WAN利用率。
    // t-bound = B + max(0,H-I0) + sum max(0,D[k-1]-Ik) + D[K-1]（K>=1）。
    // 正常非负代价且 t>0 时 ratio∈[0,1]；若全零需求使 t=0，此处仍算 0/0，
    // 当前实现未专门返回 0，会得到 NaN。这是边界说明，本次未改返回逻辑。
    double ratio = (t - bound) / t;
    struct pipeline_result_t pipe_r = {.t = t, .ratio = ratio};
    return pipe_r;
}

// pipeline：旧的 OPTION 1 方案，保留用于对比。
// 与 pipeline2 的差别：
//   - 每个置换阶段都重新做一次 balance（而不是只在开头做一次）；
//   - 计时时把"上一阶段的 dispatch + 下一阶段的 balance"与"当前阶段的
//     server 间传输"放在同一个流水级里求 max。
// 因为每个阶段都平衡，server 内总工作量更大，通常不如 pipeline2 高效。
struct pipeline_result_t GlobalScheduler::pipeline(double MBpu){
    if(decomposition_configured_)throw logic_error("complete decomposition is supported by pipeline2 only");
    if(topology_)throw logic_error("configured topology is supported by pipeline2 only");
    // return the algorithm time cost for the traffic demand matrix
    // server-level scheudling
    // cout << "start birkhoff decompostion" << endl;
    FastAll2All all2all(&mat, gpu_n, inter_type, intra_type, MBpu);
    if (enable_scaling){
        all2all.to_scaled_matrix(&balance_alpha_beta);
    }
    all2all.to_scaled_doubly_stochastic_matrix();
    all2all.decompose();

    // cout << "birkhoff decompostion succeed, pset_n:  " << all2all.p_sets.size()<<endl;

    // OPTION 1: load balance at each step
    uint locals_sz = locals.size(), pset_sz = all2all.p_sets.size();
    uint pid = 0;
    uint lid = 0;
    vector <vector<struct load_balance_result>> lbs(pset_sz, vector<struct load_balance_result>(locals_sz, {.balance=NULL, .dispatch=NULL}));

    for (pid = 0; pid != pset_sz; pid++){
        for (lid = 0; lid != locals_sz; lid ++){
            lbs[pid][lid].balance = new uint[gpu_n * gpu_n];
            lbs[pid][lid].dispatch = new uint[gpu_n * gpu_n];
            memset(lbs[pid][lid].balance, 0, sizeof(uint) * gpu_n * gpu_n);
            memset(lbs[pid][lid].dispatch, 0,  sizeof(uint) * gpu_n * gpu_n);
        }
    }
    
    // cout << "complete intra-all2all memory allocation" << endl;

    // 逐阶段生成 balance + dispatch 计划（OPTION 1 的关键区别）。
    //TODO: sort the permutation sets based on the frequency
    // generate schedule for intra-server all2all
    pid = 0;
    for (auto pset = all2all.p_sets.begin(); pset != all2all.p_sets.end(); pset++){
        lid = 0;
        for (auto local = locals.begin(); local != locals.end(); local ++){
            uint src_svr = (*local) -> get_server_id();
            auto lookup = (*pset).mp.find(src_svr);
            if (lookup == (*pset).mp.end()){
                LOG("error decomposition result");
                exit(1);
            }
            uint dst_svr = lookup -> second;
            // cout << "pid: " << pid << " , lid: "<< lid << " , src svr: " << src_svr << " , dst svr: " << dst_svr << endl;
            (*local) -> server2server_balance(dst_svr, lbs[pid][lid], (*pset).get_freq());
            lid ++;
        }
        pid++;
    }

    // cout << "complete intra-tile all2all schedule" << endl;
    // initial intra-all2all and balance
    uint prev_pid = 0, cur_pid = 1, next_pid = 2;
    double intra1, intra2, intra_time, largest_intra_time, inter, largest_intra1, largest_intra2;

    // C. 用 alpha-beta 模型估算端到端时间。
    // t     = 实际累加的总时间；
    // bound = 所有 server 间传输时间之和，代表"server 内开销全部被掩盖"时的下界。
    // evaluate the algorithm runtime
    double t = 0.0, bound = 0.0;

    // first pset
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        intra2 = spread_out(lbs[0][i].balance, gpu_n, locals[i]->get_link_info(), MBpu);
        // cout << "local all2all " << i << " : " << intra2 <<endl;
        // print_matrix(lbs[0][i].balance, gpu_n, gpu_n);
        largest_intra_time = MAX(largest_intra_time, intra2);
    }
    t += largest_intra_time;

    // pipeline the middle psets 
    for (cur_pid = 0, next_pid = cur_pid + 1, prev_pid = cur_pid - 1; 
    cur_pid < pset_sz;
    prev_pid = cur_pid, cur_pid = next_pid, next_pid++){

        largest_intra_time = 0;
        largest_intra1 = 0;
        largest_intra2 = 0;
        for (uint i = 0; i < locals_sz; i++){
            // previous step's intra-all2all dispatch
            if (cur_pid != 0){
                intra1 = spread_out(lbs[prev_pid][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
            }
            // next step's intra-all2all balance
            if (cur_pid != pset_sz - 1){
                intra2 = spread_out(lbs[next_pid][i].balance, gpu_n, locals[i]->get_link_info(), MBpu);
            }else{
                // the initial intrinsic intra all2all
                intra2 = spread_out(locals[i]->get_intrinsic_all2all(), gpu_n, locals[i]->get_link_info(), MBpu);
            }
            intra_time = intra1 + intra2;
            largest_intra_time = MAX(largest_intra_time, intra_time);
            largest_intra1 = MAX(largest_intra1, intra1);
            largest_intra2 = MAX(largest_intra2, intra2);
        }

        // cout << cur_pid << " - " << t << " , intra total: " << largest_intra_time << " , intra1: " << largest_intra1<< " , intra2: " << largest_intra2 << " , inter: " << inter <<  " ,inter bigger: " << ((inter > largest_intra_time) ? "yes" : "no") << endl;
        // current step inter-all2all inter-server transfer
        inter = permutation_set_cost(all2all.p_sets[cur_pid].get_freq(), MBpu);
        t += MAX(largest_intra_time, inter);
        bound += inter;
    }

    // 收尾：最后一段 server 内 dispatch 没有后续网络传输可以重叠，
    // 直接加到总时间上。
    // dispatch for the last pset
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        intra1 = spread_out(lbs[pset_sz-1][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
        largest_intra_time = MAX(largest_intra_time, intra1);
    }

    t += largest_intra_time;

    // cout << "FastAll2All time: "<< t << " ,bound: "<< bound << " ,extra overhead: " << (t - bound) / bound * 100 << " %"<<endl;

    // cout << "release intra-all2all memory allocation" << endl;
    // free memory for intra all2all
    for(pid = 0; pid < pset_sz; pid ++){
        for (lid = 0; lid < locals_sz; lid++){
            delete[] lbs[pid][lid].balance;
            delete[] lbs[pid][lid].dispatch;
        }
    }

    // cout << "complete release intra-all2all memory allocation" << endl;
    // ratio = (t - bound) / t：server 内开销中"没能被网络传输掩盖"的比例。
    // ratio 越小说明流水线隐藏得越好；为 0 表示 server 内工作完全被重叠。
    double ratio = (t - bound) / t;
    struct pipeline_result_t pipe_r = {.t = t, .ratio = ratio};
    return pipe_r;
}

// 一个置换阶段的 server 间代价。
// 该阶段每个源 server 的每块发送 GPU 都向自己的目的 server 发送 freq 个单元，
// 所有 server/GPU 对同时传输、互不干扰，因此单个阶段的耗时就是
// alpha + freq*beta*MBpu（每块 GPU 的网卡时间）。
// freq 已经是原始单位（PermutationSet::get_freq 已乘回 scaling_factor）。
// freq 个单元指的是数据量的粒度是吗？那么网卡时间就是单个数据 block 传输需要的传输时间。传播时间在哪里？
// 答：freq 是本 stage“每块发送 GPU 的数据单元数量/容量”，不是包数、轮数，
// 也不是整台 server 的总字节数。粒度是 MBpu：size_MB=freq*MBpu。
// 例如 freq=100、MBpu=0.01，则每 GPU 名义发送 1MB；G=8 时一条 server 配对
// 最多并发发送 8MB。各 GPU 网卡并行，所以此默认模型不再把时间乘 G 或 server 数。
// 未必每块 GPU 都填满容量；approx、缩放取整和双随机补齐都可能留下空余，
// 本函数只收 freq，因此不知道具体真实流量、配对是否空闲或是否跨 DC。
//
// alpha（us）：一次阶段通信的固定启动/延迟项，传播延迟只能被隐含在这个
// 综合参数中；beta（us/MB）：带宽倒数，size_MB*beta 是整份数据的串行化时间，
// 不是只传“第一块”的时间。config.cpp::get_inter_link_info 给出这些常量。
// 对理想单链路可理解为“首位到达需要 L，再等 S/B 让剩余数据到齐”，所以
// 最后一位到达约为 L+S/B；不是把整份数据的传播时间重复加在每个 block 上。
// RTT 是往返时间，不能直接当作这里的单向 L；ACK/拥塞窗口是否产生额外 RTT
// 开销需要更细的协议模型，本函数并没有实现这些行为。
//
// 原生 inter_info 对全部 server 配对相同，不查路径、不按 DC 分 LAN/WAN，
// 也没有模拟共享交换机链路、排队、PFC、拥塞控制。这正是后续需要用
// hooks->communication 提供拓扑代价的原因；只有传输量相同不代表异构网络耗时相同。
double GlobalScheduler::permutation_set_cost(uint freq, double MBpu){
    return inter_info.alpha + freq * inter_info.beta * MBpu;
}
