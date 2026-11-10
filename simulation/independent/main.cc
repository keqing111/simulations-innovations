#include "global.h"
#include "policy.h"
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
#include <sstream>
#include <stdexcept>

// ============================================================================
// independent/main.cc：独立策略的 CLI 入口与“原生引擎 + 自定义匹配”接线。
//
// 这个程序刻意复用冻结原生引擎的 LocalScheduler / GlobalScheduler /
// pipeline2，只通过 hooks 替换“每个 Birkhoff 阶段选哪个 matching”：
//   * defer_decomposition=true：阻止 GlobalScheduler 自己直接分解；
//   * prepare：在 balance 之后拿到 server 级剩余矩阵，构造独立 Planner；
//   * communication：每个阶段原生派发完成后，核对真实 payload 并调用拓扑计价。
// 因此机内 balance/intrinsic/dispatch 的时间仍然来自原生实现，独立部分只
// 负责跨服务器匹配与网络阶段费用。
//
// CLI 输入：N G METHOD WAN_US SERVER_BW GATEWAY_BW WAN_BW DC_CSV REPEATS
// 标准输入：N*G 阶行优先字节矩阵（对角线必须为 0）；输出 JSON 到 stdout。
// METHOD=batch 时依次运行全部七种方法（一次暖机 + 轮转重复），用于扫描。
// ============================================================================

using Clock=std::chrono::steady_clock;
using native_topology::Topology;
using native_topology::Transfer;
// 链接参数来自 reproduction/link_profile 的 “paper” 配置；这里只替换 link
// 工厂，保证机内与原生使用同一套链路模型，而不是另写一套时延参数。
link_info_t profile_get_inter_link_info(INTER_LINK_TYPE);
link_info_t profile_get_intra_link_info(INTRA_LINK_TYPE);
link_info_t get_inter_link_info(INTER_LINK_TYPE t){return profile_get_inter_link_info(t);}
link_info_t get_intra_link_info(INTRA_LINK_TYPE t){return profile_get_intra_link_info(t);}
// 可选的单方法策略；spreadout 是 GPU 级循环配对参照，不参与 Birkhoff hooks。
static const std::vector<std::string> methods={"native","bandwidth","time","maxmin","maxmin-bandwidth","maxmin-time","spreadout"};
static double elapsed(Clock::time_point t){return std::chrono::duration<double,std::micro>(Clock::now()-t).count();}
// 严格的非负整数/浮点解析：拒绝空串、负号、非数字后缀和非有限值；
// number() 还可在 zero=true 时允许 0（WAN 传播时间可以为 0）。
static uint64_t integer(const std::string& s){if(s.empty()||s.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("unsigned integer required");return std::stoull(s);}
static double number(const std::string& s,bool zero=false){size_t used=0;double x=std::stod(s,&used);if(used!=s.size()||!std::isfinite(x)||x<0||(!zero&&x==0))throw std::invalid_argument("finite parameter range");return x;}
template<class T>static void json_array(const T& xs){std::cout<<"[";bool comma=false;for(auto x:xs){if(comma)std::cout<<",";comma=true;std::cout<<x;}std::cout<<"]";}
template<class T>static void object(const T& xs){std::cout<<"{";bool comma=false;for(auto x:xs){if(comma)std::cout<<",";comma=true;std::cout<<"\""<<x.first<<"\":"<<x.second;}std::cout<<"}";}
// 一个通信阶段的原始记录：原生给出的公共系数、目的 server 映射、逐 GPU
// 实际 payload，以及拓扑计价结果（alpha/serial/共享资源字节）和机内本地时间。
struct Frame{unsigned freq=0;std::vector<unsigned> map;std::vector<uint64_t> payload;native_topology::Cost cost;double local_us=0;};
// 一次方法运行的完整结果：总计/balance/intrinsic 时间、真实跨服务器字节、
// 各阶段 dispatch 时间、阶段明细和独立策略的计数诊断。
struct Result{double total=0,balance=0,intrinsic=0,planning=0;uint64_t bytes=0;std::vector<double> dispatch;std::vector<Frame> stages;independent_fast::Counters stats;};

// 运行一种方法。前半段直接复用原生 LocalScheduler/GlobalScheduler：
//   - 每个 server 一个 LocalScheduler，负责 server 内 balance 与派发；
//   - GlobalScheduler 以 pipeline2 组合 intrinsic/communication/dispatch；
//   - 我们只通过 hooks 接管“跨服务器 matching 的选择”。
// 后半段逐阶段做两件事：核对原生实际 payload 与自己的剩余影子一致，
// 再用 topology::Topology::evaluate 计算该阶段的传播/串行化费用。
static Result run(const Topology& top,const std::vector<unsigned>& data,const std::string& method){
    Result result;unsigned n=top.n,g=top.g,r=n*g;auto start=Clock::now();
    // 真实跨服务器字节：只统计 server 不同的单元，对角线必须为 0。
    for(unsigned s=0;s<r;++s)for(unsigned d=0;d<r;++d)if(s/g!=d/g)result.bytes+=data[s*r+d];
    if(method=="spreadout"){
        // spreadout 参照：每个阶段让 GPU i 发给 (i+k)%r，共 r 个阶段。
        // 它不经过 Birkhoff/原生调度器，也不保证 perfect matching 语义；
        // 机内同 server 的字节按 intra alpha+beta 单独计时，跨 server 才计价。
        for(unsigned k=0;k<r;++k){Frame frame;for(unsigned s=0;s<r;++s){frame.map.push_back((s+k)%r);frame.payload.push_back(data[s*r+(s+k)%r]);}result.stages.push_back(std::move(frame));}
        result.planning=elapsed(start);auto intra=get_intra_link_info(H100);
        for(auto& frame:result.stages){std::vector<Transfer> transfers;
            for(unsigned s=0;s<r;++s){unsigned d=frame.map[s];auto x=frame.payload[s];if(!x)continue;
                if(s/g==d/g)frame.local_us=std::max(frame.local_us,intra.alpha+intra.beta*x*1e-6);else transfers.push_back({s/g,d/g,x});}
            frame.cost=top.evaluate(transfers);result.total+=std::max(frame.cost.total_us,frame.local_us);}
        if(!std::isfinite(result.total))throw std::overflow_error("nonfinite communication time");
        return result;
    }
    // 构造原生调度器：data.data()+s*g*r 指向第 s 个 server 的 g 行需求块。
    // 这里传 const_cast 是因为原生接口历史上接受非 const 指针，但本程序之后
    // 不会再修改输入矩阵；所有权仍留在 main 的 data 上。
    std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(const_cast<unsigned*>(data.data())+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
    GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
    // setup 到此为止，包含需求字节统计和调度器构造；planner 本身的构造/
    // 分解发生在 pipeline2 的 prepare 钩子里，计入 hooks.planning_us。
    // 两者最后相加，因此路径预处理/候选构造都不是免费操作。
    double setup=elapsed(start);pipeline_hooks_t hooks;hooks.defer_decomposition=true;
    // pending 是“每对 server 逐 GPU 待发量”的影子：由 prepare 的 rows 复制而来，
    // communication 每阶段按原生 actual 扣减，最后检查是否还有真实字节没送到。
    std::vector<uint64_t> pending;std::unique_ptr<independent_fast::Planner> planner;
    hooks.prepare=[&](Matrix&,const std::vector<uint64_t>& rows,FastAll2All& app){
        // balance 之后才会调用 prepare；rows 是 server 级、逐 GPU 的真实剩余。
        // 构造 Planner 后立即完成整个分解并把 p_sets 写回 app；原生随后只负责
        // 按这些 p_sets 派发，不再自己选 matching。
        pending=rows;planner.reset(new independent_fast::Planner(top,rows,method));planner->decompose_into(app);};
    hooks.communication=[&](const PermutationSet& p,const std::vector<uint64_t>& actual){
        // 每个阶段：p.mp 是原生实际使用的匹配，actual 是逐 GPU 实际发送量。
        // 先验证 perfect matching，再用 pending 影子核对 actual 没有被原生
        // 重新分配或丢失；两者不一致直接失败，避免“策略结果”与“实际派发”脱节。
        Frame frame;frame.freq=p.get_freq();frame.payload=actual;std::vector<Transfer> transfers;std::set<unsigned> destinations;
        for(unsigned s=0;s<n;++s){unsigned d=p.mp.at(s);if(d>=n||!destinations.insert(d).second)throw std::runtime_error("not perfect matching");frame.map.push_back(d);uint64_t amount=0;
            for(unsigned u=0;u<g;++u){auto& v=pending[(s*n+d)*g+u];auto sent=std::min<uint64_t>(v,frame.freq);if(sent!=actual[s*g+u])throw std::runtime_error("native/shadow payload mismatch");v-=sent;amount+=sent;}
            transfers.push_back({s,d,amount});}
        // 只把真实 amount 交给拓扑计价；自环/零量阶段 cost 自然为 0。
        frame.cost=top.evaluate(transfers);double cost=frame.cost.total_us;result.stages.push_back(std::move(frame));return cost;};
    // pipeline2 用原生 alpha-beta 组合 intrinsic、各阶段通信和 dispatch；
    // balance_alpha_beta 是共享的机内链路参数，1e-6 是时间单位缩放。
    result.total=scheduler.pipeline2(1e-6,&balance_alpha_beta,&hooks).t;
    // 汇总计时：planning = 调度器/策略准备 + hooks 内部规划；balance/intrinsic/
    // dispatch 直接来自原生 hooks，用于在 JSON 中区分网络收益与机内时间。
    result.planning=setup+hooks.planning_us;result.balance=hooks.balance_us;result.intrinsic=hooks.intrinsic_us;result.dispatch=hooks.dispatch_us;result.stats=planner->stats();
    // 最终守恒检查：所有真实跨 server 字节都必须已经派发；否则说明匹配或
    // 扣减逻辑出了问题，宁可报错也不输出看似合理的 CCT。
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)for(unsigned u=0;u<g;++u)if(pending[(s*n+d)*g+u])throw std::runtime_error("undelivered real bytes");
    if(!std::isfinite(result.total)||!std::isfinite(result.planning))throw std::overflow_error("nonfinite communication/planning time");
    return result;
}
// 输出单方法的 JSON 语义。审计脚本会独立重建 stages 的 alpha/serial/links，
// 并与 total_us/balance/intrinsic/dispatch 组合公式对账，所以这里的字段名和
// 含义必须稳定：freq 是公共系数，payload 是逐 GPU 实际发送量，links 是每阶段
// 各共享资源的真实字节，bandwidths 是声明容量（Gbps）。
static void output(const Result& r,const std::string& method,const Topology& top,const std::vector<double>& samples){
    std::cout<<"{\"method\":\""<<method<<"\",\"total_us\":"<<r.total<<",\"inter_bytes\":"<<r.bytes<<",\"balance_us\":"<<r.balance<<",\"intrinsic_us\":"<<r.intrinsic<<",\"payload_verified\":true,\"dispatch_us\":";json_array(r.dispatch);
    std::cout<<",\"timing_samples_us\":";json_array(samples);std::cout<<",\"topology\":{\"dc\":";json_array(top.dc);std::cout<<",\"bandwidths\":";object(top.bandwidths);
    std::cout<<"},\"stats\":{\"stages\":"<<r.stats.stages<<",\"matching_calls\":"<<r.stats.matching_calls<<",\"constructions\":"<<r.stats.constructions<<",\"accepted\":"<<r.stats.accepted
        <<",\"coefficient_rejections\":"<<r.stats.coefficient_rejections<<",\"volume_rejections\":"<<r.stats.volume_rejections<<"},\"stages\":[";
    for(unsigned k=0;k<r.stages.size();++k){if(k)std::cout<<",";auto& f=r.stages[k];std::cout<<"{\"freq\":"<<f.freq<<",\"map\":";json_array(f.map);std::cout<<",\"payload\":";json_array(f.payload);
        std::cout<<",\"alpha_us\":"<<f.cost.alpha_us<<",\"serial_us\":"<<f.cost.serial_us<<",\"communication_us\":"<<f.cost.total_us<<",\"local_us\":"<<f.local_us<<",\"links\":";object(f.cost.bytes);std::cout<<",\"bandwidths\":";object(f.cost.bandwidth_gbps);std::cout<<"}";}
    std::cout<<"]}";
}
int main(int argc,char** argv){try{
    // “paper”链路档案：server 间 400Gbps、server 内 3600Gbps（NVLink 等效）、
    // alpha 缩放 1；与冻结引擎的对照运行使用同一档案，避免把参数差异
    // 误读成策略差异。
    configure_link_profile("paper",400,3600,1);std::cout<<std::setprecision(17);
    // 特殊模式：用原生 Zipf(.9) 生成器输出 count 个无符号权重，供 study.py
    // 构造可复现的 Zipf 矩阵；不执行任何仿真。
    if(argc==4&&std::string(argv[1])=="--zipf"){
        auto seed=integer(argv[2]),count=integer(argv[3]);if(seed>UINT32_MAX||count>1048576)throw std::invalid_argument("zipf range");zipf_distribution generator(.9);std::srand(seed);std::vector<unsigned> values;generator.zipf(&values,count);json_array(values);std::cout<<"\n";return 0;}
    if(argc!=10)throw std::invalid_argument("N G METHOD WAN_US SERVER_BW GATEWAY_BW WAN_BW DC_CSV REPEATS");
    auto nn=integer(argv[1]),gg=integer(argv[2]),repeat=integer(argv[9]);if(!nn||nn>64||!gg||gg>16||!repeat||repeat>100)throw std::invalid_argument("dimension/repeat range");
    unsigned n=nn,g=gg,r=n*g;std::string method=argv[3];
    if(method!="batch"&&std::find(methods.begin(),methods.end(),method)==methods.end())throw std::invalid_argument("unknown method");
    double wan=number(argv[4],true),sb=number(argv[5]),gb=number(argv[6]),wb=number(argv[7]);
    // DC 标签必须覆盖每个 server 且落在 [0,n)；这里只记录 DC 归属，
    // 具体路径/容量由 Topology 根据 dc 推导。
    std::vector<int> dc;std::stringstream csv(argv[8]);std::string token;
    while(std::getline(csv,token,',')){auto d=integer(token);if(d>=n)throw std::invalid_argument("DC label");dc.push_back(d);}if(dc.size()!=n)throw std::invalid_argument("DC dimensions");
    // 读取 N*G 阶行优先矩阵。原生内部使用 signed int 单元，故每格必须
    // <= INT32_MAX；对角线必须为 0，否则“自匹配”语义会被输入破坏。
    std::vector<unsigned> data(r*r);for(auto& v:data){if(!(std::cin>>token))throw std::invalid_argument("truncated matrix");auto x=integer(token);if(x>INT32_MAX)throw std::invalid_argument("native signed cell limit");v=x;}
    if(std::cin>>token)throw std::invalid_argument("trailing matrix");for(unsigned s=0;s<r;++s)if(data[s*r+s])throw std::invalid_argument("nonzero diagonal");
    // 原生 balance 会按 server 行/列求和，这里提前检查 uint32 聚合不溢出，
    // 避免到了原生实现内部才发生静默回绕。
    for(unsigned s=0;s<n;++s){uint64_t row=0,col=0;for(unsigned u=0;u<g;++u)for(unsigned j=0;j<r;++j){row+=data[(s*g+u)*r+j];col+=data[j*r+s*g+u];}
        if(row>UINT32_MAX-n*g||col>UINT32_MAX-n*g)throw std::invalid_argument("native aggregate range");}
    Topology top(n,g,dc,sb,gb,wb,wan);auto selected=method=="batch"?methods:std::vector<std::string>{method};
    // 先对每个方法跑一次“参考运行”；batch 模式下七种方法共享同一输入矩阵，
    // 但每次 run() 都重新构造调度器，不复用已消耗的需求。
    std::vector<Result> reference;std::vector<std::vector<double>> samples(selected.size());for(const auto& m:selected)reference.push_back(run(top,data,m));
    // 重复运行时轮转方法顺序（k=(offset+rep)%size），减少固定顺序带来的
    // 缓存/分配器偏差；每次把完整 JSON 语义与参考运行比较（排除计时样本），
    // 任何差异都视为“重复运行改变了语义”并失败。
    for(unsigned rep=0;rep<repeat;++rep)for(unsigned offset=0;offset<selected.size();++offset){unsigned k=(offset+rep)%selected.size();auto value=run(top,data,selected[k]);
        std::ostringstream a,b;auto* old=std::cout.rdbuf(a.rdbuf());output(reference[k],selected[k],top,{});std::cout.rdbuf(b.rdbuf());output(value,selected[k],top,{});std::cout.rdbuf(old);
        if(a.str()!=b.str())throw std::runtime_error("repeat changed semantics");samples[k].push_back(value.planning);}
    // batch 输出一个以方法名为键的 JSON 对象；单方法则直接输出该对象。
    // 规划计时样本只作诊断，不进入语义比较。
    if(method=="batch")std::cout<<"{";for(unsigned k=0;k<selected.size();++k){if(method=="batch"){if(k)std::cout<<",";std::cout<<"\""<<selected[k]<<"\":";}output(reference[k],selected[k],top,samples[k]);}
    if(method=="batch")std::cout<<"}";std::cout<<"\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}}
