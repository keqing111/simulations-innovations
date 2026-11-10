#include "global.h"
#include "planner.h"
#include "link_profile.h"
#include "test.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <memory>
#include <set>
#include <stdexcept>
#ifdef FAST_SCENARIO_SCAN
#include "../scenario/config.h"
#endif

// ============================================================================
// scan/main.cc：算法扫描的 C++ 驱动。
//
// 它把核心仿真器、native_topology 模型和 scan::Planner 拼在一起：
//   1. 从 stdin 读一个 r×r 的 GPU 级流量矩阵（r=n*g），构造 LocalScheduler；
//   2. 用 GlobalScheduler + pipeline_hooks_t::defer_decomposition 接管分解；
//   3. prepare 回调里创建 Planner，让它在每个 Birkhoff 阶段选择 matching；
//   4. communication 回调按 Topology 重新计算每阶段耗时，并校验 Planner
//      预期的 payload 与原生 dispatch 实际搬运量完全一致；
//   5. 输出包含阶段、payload、阶段代价、规划计时和统计的 JSON。
//
// 编译宏决定方法集合和可选字段：
//   FAST_SCAN_EXTENDED  第二轮 13 方法（含 Q75/Q50/B11）
//   FAST_SCENARIO_SCAN  场景实验（DC 标签、容量、A1/A2）
//   FAST_ORDER_SCAN     行顺序诊断（A1D/A1P/A1DP）
//   FAST_PLANNING_SCAN  实现优化对照（-ref/-opt、SPM）
// ============================================================================

using Clock=std::chrono::steady_clock;
using native_topology::Topology;
using native_topology::Transfer;
// 每个实验轮次固定一组方法；顺序也是交错计时的轮转顺序。
#ifdef FAST_PLANNING_SCAN
static const std::vector<std::string> methods={"F000-ref","F000-opt","maxmin-ref","maxmin-opt","spreadout-ref","spreadout-opt","X10-ref","X10-opt","SPM-ref","SPM-opt","A1-ref","A1-opt"};
#elif defined(FAST_ORDER_SCAN)
static const std::vector<std::string> methods={"F000","maxmin","spreadout","A1","A1D","A1P","A1DP"};
#elif defined(FAST_SCENARIO_SCAN)
static const std::vector<std::string> methods={"F000","maxmin","spreadout","X10","X11","A1","A2"};
#elif defined(FAST_SCAN_EXTENDED)
static const std::vector<std::string> methods={"F000","F011","F111","maxmin","spreadout","X10","X01","X11","X11-4","Q75","Q50","B11","B11-4"};
#else
static const std::vector<std::string> methods={"F000","F100","F010","F001","F110","F101","F011","F111","maxmin","spreadout"};
#endif
// 核心仿真器的 get_*_link_info 被改名成 profile_get_*，这样本驱动可以在
// 外层按 "paper" 配置覆盖链路参数，而不修改 scheduler/config.cpp。
link_info_t profile_get_inter_link_info(INTER_LINK_TYPE);
link_info_t profile_get_intra_link_info(INTRA_LINK_TYPE);
link_info_t get_inter_link_info(INTER_LINK_TYPE type){return profile_get_inter_link_info(type);}
link_info_t get_intra_link_info(INTRA_LINK_TYPE type){return profile_get_intra_link_info(type);}
static double elapsed(Clock::time_point t){return std::chrono::duration<double,std::micro>(Clock::now()-t).count();}
static uint64_t integer(const std::string& s){
    if(s.empty()||s.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("expected unsigned integer");return std::stoull(s);
}
template<class T> static void numbers(const T& items){bool comma=false;std::cout<<"[";for(auto v:items){if(comma)std::cout<<",";comma=true;std::cout<<v;}std::cout<<"]";}
template<class T> static void dictionary(const T& items){bool comma=false;std::cout<<"{";for(auto v:items){if(comma)std::cout<<",";comma=true;std::cout<<"\""<<v.first<<"\":"<<v.second;}std::cout<<"}";}
// 一个置换阶段的可审计记录：频次、映射、每 GPU 实际 payload、拓扑代价。
struct Frame {unsigned freq=0;std::vector<unsigned> map;std::vector<uint64_t> payload;native_topology::Cost cost;double local_us=0;};
// 规划/预处理/构造的 CPU 墙钟时间；单位 us，不代表通信时间。
struct Timing {double planning_us=0,setup_us=0,decompose_us=0,preprocess_us=0;};
struct Result {
    double total_us=0,balance_us=0,intrinsic_us=0,wall_us=0;
    uint64_t inter_bytes=0;std::vector<uint64_t> service;std::vector<double> dispatch_us;
#ifdef FAST_ORDER_SCAN
    std::vector<uint64_t> initial_rows; // 平衡后的初始逐 GPU 剩余，用于诊断重放
#endif
    std::vector<Frame> frames;native_scan::Stats stats;Timing timing;
};

// 跑一次完整流程。spreadout 走独立的轮转型基线；其余方法走
// GlobalScheduler::pipeline2 + 延迟分解 hooks。
static Result run_once(const std::vector<unsigned>& data,unsigned n,unsigned g,const std::string& method,const Topology& top){
    Result result;unsigned r=n*g;auto start=Clock::now();
    // 基线 Spread-out：每个 GPU 每轮 s->(s+offset)%r，共 r 轮。
    // 同 server 的本地流量用 H100 链路模型单算；跨 server 流量交给 Topology。
    // 每轮取 max(跨机时间, 本地时间) 后累加，和核心 spread_out 的思想一致。
    if(method=="spreadout"||method=="spreadout-ref"||method=="spreadout-opt"){
        for(unsigned offset=0;offset<r;++offset){Frame frame;frame.map.resize(r);frame.payload.resize(r);
            for(unsigned s=0;s<r;++s){unsigned d=(s+offset)%r;frame.map[s]=d;frame.payload[s]=data[s*r+d];}
            result.frames.push_back(std::move(frame));
        }
        result.timing.planning_us=elapsed(start);
        auto intra=get_intra_link_info(H100);
        for(auto& frame:result.frames){std::vector<Transfer> flows;
            for(unsigned s=0;s<r;++s){unsigned d=frame.map[s];uint64_t bytes=frame.payload[s];if(!bytes)continue;
                if(s/g==d/g)frame.local_us=std::max(frame.local_us,intra.alpha+bytes*intra.beta*1e-6);
                else flows.push_back({s/g,d/g,bytes});}
            frame.cost=top.evaluate(flows);result.total_us+=std::max(frame.cost.total_us,frame.local_us);
        }
    }else{
        // 和核心 benchmark 一样：每个源 server 一个 LocalScheduler，然后
        // GlobalScheduler 在构造时做 balance 并汇总 server 级矩阵。
        // MBpu=1e-6：矩阵单位近似为 1 字节，便于把字节直接接进拓扑模型。
        std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
        for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(const_cast<unsigned*>(data.data())+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
        GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
        result.timing.setup_us=elapsed(start);pipeline_hooks_t hooks;hooks.defer_decomposition=true;
        std::vector<uint64_t> pending;std::unique_ptr<native_scan::Planner> planner;
        // prepare 在 GlobalScheduler 完成一次 balance 后被调用；rows 是
        // [(源 server*n+目的 server)*g+源 GPU] 的真实剩余需求。
        // 这里接管本来由 FastAll2All 做的分解，由 Planner 逐阶段决定 matching。
        hooks.prepare=[&](Matrix& matrix,const std::vector<uint64_t>& rows,FastAll2All& app){
            // defer=true 时 pipeline2 必须尚未分解；否则说明调用顺序/接口被误用。
            if(!app.p_sets.empty()||hooks.native_decompose_us!=0)throw std::runtime_error("discarded decomposition before scan");
            pending=rows;result.service.resize(n*n);for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)result.service[s*n+d]=matrix.get(s,d);
#ifdef FAST_ORDER_SCAN
            result.initial_rows=rows;
#endif
            auto stamp=Clock::now();planner.reset(new native_scan::Planner(top,rows,method));
            result.timing.preprocess_us=planner->preprocess_us;planner->decompose_into(app);
            result.timing.decompose_us=elapsed(stamp);
        };
        // 每个置换阶段被 pipeline2 计价时调用。先按 pending 影子账本验证
        // “Planner 认为本阶段应发出的每 GPU 字节”与原生 dispatch 的 actual
        // 完全一致，再用 Topology 算跨机代价；同机 flow 不计入 Topology。
        hooks.communication=[&](const PermutationSet& p,const std::vector<uint64_t>& actual){
            Frame frame;frame.freq=p.get_freq();frame.payload=actual;std::vector<Transfer> flows;std::set<unsigned> destinations;
            for(unsigned s=0;s<n;++s){unsigned d=p.mp.at(s);if(d>=n||!destinations.insert(d).second)throw std::runtime_error("invalid permutation");
                frame.map.push_back(d);uint64_t bytes=0;
                for(unsigned u=0;u<g;++u){auto& left=pending[(s*n+d)*g+u];uint64_t sent=std::min<uint64_t>(frame.freq,left);
                    if(actual[s*g+u]!=sent)throw std::runtime_error("native payload shadow mismatch");left-=sent;bytes+=sent;}
                flows.push_back({s,d,bytes});
            }
            frame.cost=top.evaluate(flows);double cost=frame.cost.total_us;result.frames.push_back(std::move(frame));return cost;
        };
        result.total_us=scheduler.pipeline2(1e-6,&balance_alpha_beta,&hooks).t;
        result.timing.planning_us=result.timing.setup_us+hooks.planning_us;
        result.balance_us=hooks.balance_us;result.intrinsic_us=hooks.intrinsic_us;result.dispatch_us=std::move(hooks.dispatch_us);
        result.stats=planner->stats();
        // pipeline2 返回后，所有真实跨机需求都应被某个阶段取走。
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)for(unsigned u=0;u<g;++u)
            if(pending[(s*n+d)*g+u])throw std::runtime_error("native dispatch left true payload");
    }
    // 原始矩阵中所有跨 server 的单元总数（不扣除虚拟补齐）。
    for(unsigned s=0;s<r;++s)for(unsigned d=0;d<r;++d)if(s/g!=d/g)result.inter_bytes+=data[s*r+d];
    result.wall_us=elapsed(start);return result;
}
// 交错重复测量时，用来确认“同一方法在同一输入上语义完全一致”；
// 只比较调度结果（阶段、映射、payload、时间），不比较 CPU 计时。
static bool same(const Result& a,const Result& b){
    if(a.total_us!=b.total_us||a.frames.size()!=b.frames.size()||a.dispatch_us!=b.dispatch_us)return false;
    for(unsigned k=0;k<a.frames.size();++k)if(a.frames[k].freq!=b.frames[k].freq||a.frames[k].map!=b.frames[k].map||a.frames[k].payload!=b.frames[k].payload)return false;
    return true;
}
#ifdef FAST_ORDER_SCAN
// 导出式诊断（不影响调度）：从初始真实剩余和最终 stage 序列重建每阶段
// 的残余矩阵，再调用 support_summary 统计 q* 支持图的自由度。
static void diagnostics(const Result& result,unsigned n,unsigned g){
    std::cout<<"[";std::vector<unsigned> a(n*n);auto real=result.initial_rows;
    for(const auto& f:result.frames)if(f.map.size()==n)for(unsigned s=0;s<n;++s)a[s*n+f.map[s]]+=f.freq;
    for(unsigned k=0;k<result.stats.decisions.size();++k){if(k)std::cout<<",";const auto& f=result.frames[k];
        std::vector<int> m(f.map.begin(),f.map.end());auto info=native_scan::support_summary(a,n,result.stats.decisions[k].exact_weight,m,real,g);
        std::cout<<"{\"edges\":"<<info.edges<<",\"flexible_edges\":"<<info.flexible_edges<<",\"movable_rows\":"<<info.movable_rows
                 <<",\"real_movable_rows\":"<<info.real_movable_rows<<",\"degrees\":";numbers(info.degrees);std::cout<<"}";
        for(unsigned s=0;s<n;++s){unsigned d=f.map[s];a[s*n+d]-=f.freq;for(unsigned u=0;u<g;++u){auto& v=real[(s*n+d)*g+u];v-=std::min<uint64_t>(v,f.freq);}}
    }
    std::cout<<"]";
}
#endif
// 输出 JSON。字段分几组：
//   total_us 等是模型时间；planning/setup/decompose 是 CPU 墙钟；
//   inter_bytes/service/stages 是输入与调度审计；
//   decisions/stats 是算法搜索过程；
//   timing_samples 是交错重复的规划计时样本。
// 宏控制的字段只在对应实验构建中出现，默认结构保持不变。
static void output(const std::string& method,const Result& result,const std::vector<Timing>& samples,const Topology& top){
    const auto& stats=result.stats;
    native_scan::Options options=native_scan::Options::parse(method);
    // 顶层字段分组：总/balance/内在时间、墙钟、跨机字节、搜索计数、策略开关。
    // native_decompose_us/extra_planner_us/repairs 固定为 0，是为了让各实验变体
    // 的 JSON 字段结构一致，Python 侧可以统一解析。
    std::cout<<"{\"mode\":\""<<method<<"\",\"total_us\":"<<result.total_us<<",\"balance_us\":"<<result.balance_us<<",\"intrinsic_us\":"<<result.intrinsic_us
        <<",\"native_decompose_us\":0,\"extra_planner_us\":0,\"wall_us\":"<<result.wall_us<<",\"inter_bytes\":"<<result.inter_bytes
        <<",\"payload_verified\":true,\"cycle_trials\":"<<stats.cycle_trials<<",\"changed_matches\":"<<stats.changed_matches<<",\"repairs\":0"
        <<",\"decomposition_runs\":"<<stats.decomposition_runs<<",\"threshold_probes\":"<<stats.threshold_probes<<",\"matching_calls\":"<<stats.matching_calls
        <<",\"lower_coefficient_rejections\":"<<stats.lower_coefficient_rejections<<",\"infeasible_cycles\":"<<stats.infeasible_cycles<<",\"nonimproving_rejections\":"<<stats.nonimproving_rejections
        <<",\"policy\":{\"exact\":"<<(options.exact?"true":"false")<<",\"link\":"<<(options.link?"true":"false")
        <<",\"residual\":"<<(options.residual?"true":"false")<<",\"tail_score\":"<<(options.tail_score?"true":"false")
        <<",\"quota_numerator\":"<<options.quota_numerator<<",\"quota_denominator\":"<<options.quota_denominator
        <<",\"cycle_budget\":"<<(options.link?options.cycle_budget:0);
#ifdef FAST_SCENARIO_SCAN
    std::cout<<",\"constructive_budget\":"<<options.constructive_budget;
#endif
#ifdef FAST_ORDER_SCAN
    std::cout<<",\"row_order\":"<<options.row_order;
#endif
#ifdef FAST_PLANNING_SCAN
    std::cout<<",\"optimized\":"<<(options.optimized?"true":"false")<<",\"static_path\":"<<(options.static_path?"true":"false");
#endif
    std::cout<<"}";
    // 以下字段只在对应实验宏开启时输出；默认构建保持最小集合。
#ifdef FAST_SCENARIO_SCAN
    // scenario 实验额外记录构造式匹配次数与拓扑参数（DC 标签、各资源带宽）。
    std::cout<<",\"construction_trials\":"<<stats.construction_trials<<",\"construction_accepted\":"<<stats.construction_accepted
             <<",\"topology\":{\"dc\":";numbers(top.dc);std::cout<<",\"bandwidths\":";dictionary(top.bandwidths);std::cout<<"}";
#endif
#ifdef FAST_ORDER_SCAN
    // Export-only diagnosis: all warmups/measured planning runs are finished.
    // 这里才重建残余矩阵做诊断，确保不会干扰任何已测量的规划轮次。
    std::cout<<",\"initial_rows\":";numbers(result.initial_rows);std::cout<<",\"support_diagnostics\":";diagnostics(result,top.n,top.g);
#endif
    // service/dispatch 是每个 server/GPU 的载荷划分；timing_samples 按每次
    // 交错重复记录 CPU 规划时间，decisions 则按每个 Birkhoff 阶段记录选择。
    std::cout<<",\"service\":";numbers(result.service);std::cout<<",\"dispatch_us\":";numbers(result.dispatch_us);std::cout<<",\"timing_samples\":[";
    for(unsigned k=0;k<samples.size();++k){if(k)std::cout<<",";auto t=samples[k];std::cout<<"{\"planning_us\":"<<t.planning_us<<",\"setup_us\":"<<t.setup_us
        <<",\"decompose_us\":"<<t.decompose_us<<",\"preprocess_us\":"<<t.preprocess_us<<"}";}
    std::cout<<"],\"decisions\":[";
    // 每个 Birkhoff 阶段一条：threshold 是支持阈值 q，weight 是本轮公共系数，
    // native_weight/exact_weight 分别是原始匈牙利匹配与精确 Max-Min 的得分。
    for(unsigned k=0;k<stats.decisions.size();++k){if(k)std::cout<<",";auto d=stats.decisions[k];
        std::cout<<"{\"threshold\":"<<d.threshold<<",\"seed_weight\":"<<d.seed_weight<<",\"weight\":"<<d.weight<<",\"probes\":"<<d.probes
            <<",\"native_weight\":"<<d.native_weight<<",\"exact_weight\":"<<d.exact_weight;
#ifdef FAST_SCENARIO_SCAN
        std::cout<<",\"constructions\":"<<d.constructions;
#endif
#ifdef FAST_ORDER_SCAN
        std::cout<<",\"row_order\":";numbers(d.row_order);std::cout<<",\"row_pressure\":";numbers(d.row_pressure);
#endif
        std::cout<<"}";}
    std::cout<<"],\"stages\":[";
    // stage 序列是最终调度计划本身：freq 为阶段频次，map 为 server 级置换，
    // payload 是逐 GPU 实际发送量；alpha_us/serial_us 是拓扑模型分解出的
    // 固定延迟与串行传输时间，links/bandwidths 记录使用的资源路径。
    for(unsigned k=0;k<result.frames.size();++k){if(k)std::cout<<",";const auto& f=result.frames[k];
        std::cout<<"{\"freq\":"<<f.freq<<",\"map\":";numbers(f.map);std::cout<<",\"payload\":";numbers(f.payload);
        std::cout<<",\"alpha_us\":"<<f.cost.alpha_us<<",\"serial_us\":"<<f.cost.serial_us<<",\"communication_us\":"<<f.cost.total_us<<",\"local_us\":"<<f.local_us
            <<",\"links\":";dictionary(f.cost.bytes);std::cout<<",\"bandwidths\":";dictionary(f.cost.bandwidth_gbps);std::cout<<"}";}
    std::cout<<"]}";
}
int main(int argc,char** argv){
    try{
        // 统一使用论文 profile：服务器间 400 Gbps、服务器内 3600 Gbps 等效带宽。
        // 具体覆盖逻辑在 reproduction/link_profile.cc；这里只是复用它。
        configure_link_profile("paper",400,3600,1);std::cout<<std::setprecision(17);
        // 辅助模式：输出 Zipf(.9) 权重，供 Python 测试脚本生成与 C++ 相同的输入。
        if(argc==4&&std::string(argv[1])=="--zipf"){
            auto seed=integer(argv[2]),count=integer(argv[3]);if(seed>UINT32_MAX||count>1048576)throw std::invalid_argument("zipf range");
            zipf_distribution generator(.9); // Its constructor seeds from the clock.
            std::srand(seed);std::vector<unsigned> values;generator.zipf(&values,count);numbers(values);std::cout<<"\n";return 0;}
        // 两种调用方式：
        //   scan N G METHOD WAN_US [REPEATS]  单方法
        //   scan --batch N G WAN_US REPEATS   运行当前构建允许的全部方法
        // 矩阵始终从 stdin 读取（空白分隔的整数）。
        bool batch=argc>1&&std::string(argv[1])=="--batch";
        bool extended=false;
#ifdef FAST_SCENARIO_SCAN
        extended=argc==11;
#endif
        if(!extended&&((batch&&argc!=6)||(!batch&&argc!=5&&argc!=6)))throw std::invalid_argument("usage: scan N G METHOD WAN_US [REPEATS] or --batch N G WAN_US REPEATS; scenario accepts DC_CSV SERVER_BW GATEWAY_BW WAN_BW PROFILE");
        uint64_t nn=integer(argv[batch?2:1]),gg=integer(argv[batch?3:2]);
        if(!nn||nn>64||!gg||gg>16)throw std::invalid_argument("dimension range");unsigned n=nn,g=gg,r=n*g;
        std::string method=batch?"":argv[3],latency=argv[4];size_t used=0;double wan=std::stod(latency,&used);
        if(used!=latency.size())throw std::invalid_argument("invalid latency");
        uint64_t requested_repeats=argc>=6?integer(argv[5]):1;
        if(!requested_repeats||requested_repeats>1000)throw std::invalid_argument("repeat range");
        unsigned repeats=unsigned(requested_repeats);
        if(!batch&&std::find(methods.begin(),methods.end(),method)==methods.end())throw std::invalid_argument("method range");
        // 读完整的 r×r 矩阵；只接受非负整数，且单元格必须落在本机有符号
        // 中间量能承受的范围内。对角线必须为 0（不允许自环需求）。
        std::vector<unsigned> data(r*r);std::string token;
        for(auto& v:data){if(!(std::cin>>token))throw std::invalid_argument("matrix truncated");uint64_t x=integer(token);
            if(x>INT32_MAX)throw std::overflow_error("native signed cell range");v=x;}
        if(std::cin>>token)throw std::invalid_argument("trailing data");
        for(unsigned s=0;s<r;++s)if(data[s*r+s])throw std::invalid_argument("nonzero diagonal");
        // 预检查每个 server 的行/列总量，避免进入原生 uint 路径后溢出。
        for(unsigned s=0;s<n;++s){uint64_t row=0,col=0;for(unsigned u=0;u<g;++u)for(unsigned j=0;j<r;++j){row+=data[(s*g+u)*r+j];col+=data[j*r+s*g+u];}
            if(row>UINT32_MAX-n*g||col>UINT32_MAX-n*g)throw std::overflow_error("native server aggregate range");}
        // 默认把 server 均匀分到 3 个 DC；scenario 扩展可用 CSV 覆盖。
        std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=std::min(2U,s*3/n);
        double server_bw=400,gateway_bw=400,wan_bw=100;
#ifdef FAST_SCENARIO_SCAN
        if(extended){dc=native_scenario::labels(argv[6],n);server_bw=native_scenario::capacity(argv[7]);gateway_bw=native_scenario::capacity(argv[8]);wan_bw=native_scenario::capacity(argv[9]);}
#endif
        Topology top(n,g,dc,server_bw,gateway_bw,wan_bw,wan);
#ifdef FAST_SCENARIO_SCAN
        if(extended)native_scenario::capacities(top,argv[10],wan_bw);
#endif
        std::vector<std::string> selected=batch?methods:std::vector<std::string>{method};
        std::vector<Result> reference;std::vector<std::vector<Timing>> samples(selected.size());
        // 先让每个方法跑一次作为语义参照（同时充任暖机）；正式计时从下一轮开始。
        for(const auto& name:selected)reference.push_back(run_once(data,n,g,name,top)); // warm-up, not a measurement.
        // 交错轮转测量，避免“先跑完 A 再跑完 B”带来的系统漂移；
        // 每次结果都必须和参照语义一致，否则说明存在不稳定/未初始化状态。
        for(unsigned repeat=0;repeat<repeats;++repeat)for(unsigned offset=0;offset<selected.size();++offset){
            unsigned k=(offset+repeat)%selected.size();auto value=run_once(data,n,g,selected[k],top);
            if(!same(reference[k],value))throw std::runtime_error("repeat changed semantic schedule");samples[k].push_back(value.timing);
        }
        if(batch)std::cout<<"{";
        for(unsigned k=0;k<selected.size();++k){if(batch){if(k)std::cout<<",";std::cout<<"\""<<selected[k]<<"\":";}output(selected[k],reference[k],samples[k],top);}
        if(batch)std::cout<<"}";std::cout<<"\n";
    }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
