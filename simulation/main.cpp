#include <iostream>
#include "matrix.h"
#include "all2all.h"
#include "local.h"
#include "global.h"
#include "test.h"
#include "config.h"
#include "topology/model.h"
#include <iomanip>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <cmath>
#include <climits>


using namespace std;

// Optional single-case entry; no arguments still run the original benchmark below.
static int topology_run(int argc,char** argv) {
    if(argc!=11)throw invalid_argument("--topology N G native|bandwidth|time WAN_US SERVER_BW GATEWAY_BW WAN_BW MBpu DC_CSV");
    auto integer=[](const string& s) {
        if(s.empty()||s.find_first_not_of("0123456789")!=string::npos)throw invalid_argument("unsigned integer required");
        return stoull(s);
    };
    auto number=[](const string& s,bool zero=false) {
        size_t used=0;double x=stod(s,&used);
        if(used!=s.size()||!isfinite(x)||x<0||(!zero&&x==0))throw invalid_argument("finite parameter range");
        return x;
    };
    auto nn=integer(argv[2]),gg=integer(argv[3]);
    if(!nn||nn>64||!gg||gg>16)throw invalid_argument("N/G range");
    uint n=nn,g=gg,r=n*g;string name=argv[4];ScheduleMode mode=ScheduleMode::Native;
    if(name=="bandwidth")mode=ScheduleMode::BandwidthAware;
    else if(name=="time")mode=ScheduleMode::CompletionTimeAware;
    else if(name!="native")throw invalid_argument("unknown schedule");
    vector<int> dc;stringstream csv(argv[10]);string token;
    while(getline(csv,token,',')){auto d=integer(token);if(d>=n)throw invalid_argument("DC label");dc.push_back(d);}
    auto top=native_topology::Topology(n,g,dc,number(argv[6]),number(argv[7]),number(argv[8]),number(argv[5],true));
    // argv[9] is MB per native unit, explicitly separating matrix units from bytes.
    double MBpu=number(argv[9]);vector<uint> data(r*r);
    for(auto& x:data){if(!(cin>>token))throw invalid_argument("truncated matrix");auto v=integer(token);if(v>INT_MAX)throw invalid_argument("matrix range");x=v;}
    if(cin>>token)throw invalid_argument("trailing matrix values");
    for(uint s=0;s<r;++s)if(data[s*r+s])throw invalid_argument("nonzero GPU diagonal");
    for(uint s=0;s<n;++s) {
        uint64_t out=0,in=0;
        for(uint u=0;u<g;++u)for(uint d=0;d<r;++d){out+=data[(s*g+u)*r+d];in+=data[d*r+s*g+u];}
        if(out>UINT32_MAX-r||in>UINT32_MAX-r)throw invalid_argument("native aggregate range");
    }
    vector<unique_ptr<LocalScheduler>> owners;vector<LocalScheduler*> locals;
    for(uint s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
    GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,MBpu);scheduler.set_schedule(mode,&top);
    pipeline_hooks_t trace;auto result=scheduler.pipeline2(MBpu,&balance_alpha_beta,&trace);
    if(!isfinite(result.t))throw overflow_error("nonfinite total time");
    cout<<setprecision(17)<<"{\"total_us\":"<<result.t<<",\"balance_us\":"<<trace.balance_us
        <<",\"intrinsic_us\":"<<trace.intrinsic_us<<",\"dispatch_us\":[";
    for(size_t k=0;k<trace.dispatch_us.size();++k){if(k)cout<<",";cout<<trace.dispatch_us[k];}
    cout<<"],\"stages\":[";
    for(size_t k=0;k<trace.stages.size();++k) {
        if(k)cout<<",";const auto& p=trace.stages[k];vector<native_topology::GpuTransfer> flows;
        for(uint s=0;s<n;++s)if(s!=p.mp.at(s))for(uint u=0;u<g;++u)
            flows.push_back({s*g+u,p.mp.at(s)*g+u,double(trace.payload_units[k][s*g+u])*MBpu*1e6});
        auto cost=top.evaluate_gpu(flows);
        cout<<"{\"freq\":"<<p.get_freq()<<",\"map\":[";
        for(uint s=0;s<n;++s){if(s)cout<<",";cout<<p.mp.at(s);}
        cout<<"],\"payload\":[";
        for(size_t i=0;i<trace.payload_units[k].size();++i){if(i)cout<<",";cout<<trace.payload_units[k][i];}
        cout<<"],\"alpha_us\":"<<cost.alpha_us<<",\"serial_us\":"<<cost.serial_us
            <<",\"communication_us\":"<<cost.total_us<<"}";
    }
    cout<<"]}\n";return 0;
}

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

int main(int argc,char** argv){
    if(argc>1) {
        try {
            if(string(argv[1])!="--topology")throw invalid_argument("unknown option");
            return topology_run(argc,argv);
        } catch(const exception& e){cerr<<e.what()<<"\n";return 2;}
    }

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

    // 构造实验对象：2 个 server、每 server 16 GPU、重复 20 次、关闭 scaling，scaling 指的矩阵缩放是什么？
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
