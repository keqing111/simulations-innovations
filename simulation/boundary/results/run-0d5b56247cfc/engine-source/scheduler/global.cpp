#include <cstring>
#include "global.h"
#include "define.h"
#include <chrono>
#include <iostream>
#include <stdexcept>
using namespace chrono;
using namespace std;

// ============================================================================
// GlobalScheduler 的实现：把 server 级分解与 server 内调度拼成一条流水线，
// 再用 alpha-beta 模型逐阶段累加时间。
//
// 计时模型的核心思想（pipeline2）：
//   - server 间传输和 server 内搬运可以重叠（流水线）；
//   - 同一个 server 的 server 内链路是共享的，所以它所有目的 server 的
//     balance/dispatch 时间要串行相加，再取所有源 server 的最大值；
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
GlobalScheduler::GlobalScheduler(uint _server_n, uint _gpu_n, vector<LocalScheduler*> _locals, INTER_LINK_TYPE type, INTRA_LINK_TYPE itype, bool scaling, double MBpu){
    server_n = _server_n;
    gpu_n = _gpu_n;
    locals = _locals;
    enable_scaling = scaling;
    inter_type = type;
    inter_info = get_inter_link_info(type);
    intra_type = itype;
    intra_info = get_intra_link_info(itype);
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
                // 决定不 balance：用最忙 GPU 的量作为该目的 server 的代表值
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

// 同上，但直接接收自定义的链路参数（用于扫描带宽/拓扑的实验）。
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
// pipeline3：server 内调度结构与 pipeline2 相同（只 balance 一次 + 每阶段 dispatch），
// 区别是 server 内耗时由调用者传入的 fn 计算，而不是固定用 spread_out。
// 这样可以在 topology_benchmark 里评估全互联、双环等不同拓扑对 FAST 的影响。
// 注意：pipeline3 不做 scaling（即使构造时 enable_scaling=true），也不支持 hooks。
struct pipeline_result_t GlobalScheduler::pipeline3(double MBpu, intra_transfer_topo_fn fn){
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
    // 对每个源 server，从剩余数据中提取 freq 的量，并记录为了对齐发送
    // 需要的 GPU->GPU 搬运（ds[阶段][server]）。
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
// 分四个阶段：
//   A. 分解：可选 scaling，然后双随机化 + Birkhoff 分解，得到 p_sets；
//   B. 制定 server 内计划：
//        - 对所有目的 server 做一次 balance（除非构造时判定可以跳过）；
//        - 对每个置换阶段，从当前剩余数据中 dispatch 出该阶段的发送量；
//   C. 计时（alpha-beta 模型）：
//        - balance 与 intrinsic all-to-all 是 start-up 开销；
//        - 中间每个阶段：本阶段 server 间传输 与 上一阶段的 server 内 dispatch 重叠；
//        - 最后补上最后一段 dispatch；
//   D. 返回总时间 t 和"未重叠开销比例" ratio。
//
// hooks 非空时额外记录各阶段耗时和 payload，供研究目录下的实验使用；
// hooks 为空时不启用任何插桩，走默认的分解和通信代价。
// ============================================================================
struct pipeline_result_t GlobalScheduler::pipeline2(double MBpu, scaling_success_condition fn, pipeline_hooks_t* hooks){
    auto planning_start = steady_clock::now();
    if (hooks) {
        hooks->native_decompose_us = 0;
        hooks->planning_us = 0;
        if (hooks->defer_decomposition && !hooks->prepare)
            throw invalid_argument("deferred decomposition requires prepare callback");
    }
    // A. server 级分解：缩放（可选）-> 双随机化 -> Birkhoff 分解
    FastAll2All all2all(&mat, gpu_n, inter_type, intra_type, MBpu);
    if (enable_scaling){
        all2all.to_scaled_matrix(fn);
    }
    if (!hooks || !hooks->defer_decomposition) {
        auto decomposition_start = steady_clock::now();
        all2all.to_scaled_doubly_stochastic_matrix();
        all2all.decompose();
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
    uint locals_sz = locals.size(), pset_sz = all2all.p_sets.size();
    uint pid = 0;
    uint lid = 0;

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

    // 研究钩子：把 balance 之后每个 (源 server, 目的 server, 源 GPU) 的
    // 剩余 payload 拍平成 rows，交给 prepare 回调；回调可以重新决定分解/调度。
    // 之后 stages/payload_units 会记录最终采用的阶段和实际搬运量。
    if (hooks) {
        hooks->dispatch_us.clear(); hooks->payload_units.clear();
        vector<uint64_t> rows(server_n * server_n * gpu_n);
        for (uint s=0; s<server_n; ++s) for (uint d=0; d<server_n; ++d)
            for (uint i=0; i<gpu_n; ++i)
                rows[(s * server_n + d) * gpu_n + i] = locals[s]->pending_units(d,i);
        if (hooks->prepare) hooks->prepare(mat, rows, all2all);
        hooks->stages = all2all.p_sets;
        pset_sz = all2all.p_sets.size();
        hooks->payload_units.assign(pset_sz, vector<uint64_t>(server_n * gpu_n));
    }
    vector <vector<struct load_balance_result>> ds(pset_sz, vector<struct load_balance_result>(locals_sz, {.balance=NULL, .dispatch=NULL}));
    for (pid=0; pid<pset_sz; ++pid) for (lid=0; lid<locals_sz; ++lid) {
        ds[pid][lid].dispatch = new uint[gpu_n * gpu_n];
        memset(ds[pid][lid].dispatch,0,sizeof(uint) * gpu_n * gpu_n);
    }

    // 生成每个置换阶段的 server 内 dispatch 计划：
    // 置换集合 mp 给出本阶段"源 server -> 目的 server"的对应关系；
    // 对每个源 server，从剩余数据中提取 freq 的量，并记录为了对齐发送
    // 需要的 GPU->GPU 搬运（ds[阶段][server]）。
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
            vector<uint64_t> before;
            if (hooks) for(uint i=0; i<gpu_n; ++i) before.push_back((*local)->pending_units(dst_svr,i));
            (*local) -> server2server_dispatch(dst_svr, ds[pid][lid], (*pset).get_freq());
            if (hooks) for(uint i=0; i<gpu_n; ++i)
                hooks->payload_units[pid][src_svr * gpu_n + i] = before[i] - (*local)->pending_units(dst_svr,i);
            lid ++;
        }
        pid++;
    }
    if (hooks) hooks->planning_us = duration<double, std::micro>(steady_clock::now()-planning_start).count();
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

    // 启动阶段 1：一次性 balance。
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
            balance_time += cur_spd;
            // cout << "first balance: "<< endl;
            // print_matrix(bs[i][s].balance, gpu_n, gpu_n);


        }
        largest_intra_time = MAX(largest_intra_time, balance_time);
    }
    t += largest_intra_time;

    // cout << "balance time: " << t << endl;
    if (hooks) hooks->balance_us = largest_intra_time;
    // 启动阶段 2：第一段 server 间传输，同时可以和"本 server 内部 all-to-all"
    // （intrinsic_all2all，即源/目的在同一 server 的流量）重叠，因此取两者最大值。
    // 空置换集合（没有跨 server 需求）时 inter 直接为 0。
    // communication 是本阶段 server 间代价的来源：默认 alpha + freq*beta*MBpu，
    // hooks 可以用自定义模型覆盖。
    // first inter transfer; empty inter-server demand needs no permutation.
    auto communication = [&](uint k) {
        return hooks && hooks->communication ? hooks->communication(all2all.p_sets[k], hooks->payload_units[k])
            : permutation_set_cost(all2all.p_sets[k].get_freq(), MBpu);
    };
    inter = pset_sz ? communication(0) : 0;
    // intrinsic all2all
    largest_intra_time = 0;
    for (uint i = 0; i < locals_sz; i++){
        intra1 = spread_out(locals[i]->get_intrinsic_all2all(), gpu_n, locals[i]->get_link_info(), MBpu);
        largest_intra_time = MAX(largest_intra_time, intra1);
    }

    if (hooks) hooks->intrinsic_us = largest_intra_time;
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
                intra1 = spread_out(ds[prev_pid][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
            }
            largest_intra_time = MAX(largest_intra_time, intra1);
        }

        // cout << cur_pid << " - " << t << " , intra: " << largest_intra_time<< " , inter: " << inter <<  " ,inter bigger: " << ((inter > largest_intra_time) ? "yes" : "no") << endl;
        // current step inter-all2all inter-server transfer
        inter = communication(cur_pid);
        if (hooks) hooks->dispatch_us.push_back(largest_intra_time);
        t += MAX(largest_intra_time, inter);
        bound += inter;
    }

    // 收尾：最后一段 server 内 dispatch 没有后续网络传输可以重叠，
    // 直接加到总时间上。
    // dispatch for the last pset
    largest_intra_time = 0;
    for (uint i = 0; pset_sz && i < locals_sz; i++){
        intra1 = spread_out(ds[pset_sz-1][i].dispatch, gpu_n, locals[i]->get_link_info(), MBpu);
        largest_intra_time = MAX(largest_intra_time, intra1);
    }
    t += largest_intra_time;
    if (hooks && pset_sz) hooks->dispatch_us.push_back(largest_intra_time);

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

// pipeline：旧的 OPTION 1 方案，保留用于对比。
// 与 pipeline2 的差别：
//   - 每个置换阶段都重新做一次 balance（而不是只在开头做一次）；
//   - 计时时把"上一阶段的 dispatch + 下一阶段的 balance"与"当前阶段的
//     server 间传输"放在同一个流水级里求 max。
// 因为每个阶段都平衡，server 内总工作量更大，通常不如 pipeline2 高效。
struct pipeline_result_t GlobalScheduler::pipeline(double MBpu){
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
double GlobalScheduler::permutation_set_cost(uint freq, double MBpu){
    return inter_info.alpha + freq * inter_info.beta * MBpu;
}
