#include "algorithm/all2all.h"
#include "scheduler/global.h"
#include "topology/model.h"
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <ctime>
#include <fstream>

// 实验入口只连接现有整数分解和FAST流水线，不复制匹配或计时算法。
namespace {
uint64_t integer(const std::string& s) {
    if(s.empty()||s.find_first_not_of("0123456789")!=std::string::npos)
        throw std::invalid_argument("unsigned integer required");
    size_t used=0;auto v=std::stoull(s,&used);
    if(used!=s.size())throw std::invalid_argument("invalid integer");
    return v;
}
double number(const std::string& s) {
    size_t used=0;double v=std::stod(s,&used);
    if(used!=s.size()||!std::isfinite(v)||v<=0)
        throw std::invalid_argument("positive finite number required");
    return v;
}
template<class T> void json_array(const std::vector<T>& v) {
    std::cout<<'[';for(size_t i=0;i<v.size();++i){if(i)std::cout<<',';std::cout<<v[i];}std::cout<<']';
}
native_topology::Topology experiment_topology(unsigned n,unsigned g,unsigned profile) {
    std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
    native_topology::Topology base(n,g,dc,400,400,100,50);
    if(!profile)return base;
    std::vector<native_topology::ResourceInfo> resources;
    for(unsigned e=0;e<base.resource_count();++e) {
        auto r=base.resource(e);
        if(profile==1&&r.name.find("gateway-")==0)r.bandwidth_gbps=100;
        if(profile==2&&r.name.find("wan:")==0) {
            int a=r.name[4]-'0',b=r.name[6]-'0';
            if((a==0&&b==1)||(a==1&&b==0))r.bandwidth_gbps=50;
            if((a==0&&b==2)||(a==2&&b==0))r.bandwidth_gbps=200;
        }
        resources.push_back(r);
    }
    std::vector<native_topology::PathInfo> paths;
    for(unsigned s=0;s<n*g;++s)for(unsigned d=0;d<n*g;++d) {
        auto p=base.gpu_path(s,d);
        int a=dc[s/g],b=dc[d/g];
        if(profile==2&&a!=b) {
            p.propagation_us=((a==0&&b==1)||(a==1&&b==0))?202.8:
                             ((a==0&&b==2)||(a==2&&b==0))?7.8:52.8;
            p.rtt_us=2*p.propagation_us;
        }
        paths.push_back(p);
    }
    auto result=native_topology::Topology::from_fixed_routes(n,g,resources,paths);
    result.dc=dc;return result;
}
void topology_json(const native_topology::Topology& top) {
    std::cout<<"{\"n\":"<<top.n<<",\"g\":"<<top.g<<",\"dc\":";json_array(top.dc);
    std::cout<<",\"resource_names\":[";
    for(unsigned e=0;e<top.resource_count();++e){if(e)std::cout<<',';std::cout<<'"'<<top.resource(e).name<<'"';}
    std::cout<<"],\"bandwidth_gbps\":[";
    for(unsigned e=0;e<top.resource_count();++e){if(e)std::cout<<',';std::cout<<top.resource(e).bandwidth_gbps;}
    std::cout<<"],\"server_paths\":[";
    for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d) {
        if(s||d)std::cout<<',';const auto& p=top.gpu_path(s*top.g,d*top.g);
        std::cout<<"{\"propagation_us\":"<<p.propagation_us<<",\"rtt_us\":"<<p.rtt_us<<",\"resource_ids\":";
        json_array(p.resource_ids);std::cout<<'}';
    }
    std::cout<<"]}";
}
void stats_json(const DecompositionStats& stats) {
    std::cout<<"\"resource_proposals\":"<<stats.resource_proposals
        <<",\"resource_accepts\":"<<stats.resource_accepts
        <<",\"cycle_evaluations\":"<<stats.cycle_evaluations<<",\"cycle_accepts\":"<<stats.cycle_accepts
        <<",\"cycle_passes\":"<<stats.cycle_passes<<',';
    std::cout<<"\"cpu_us\":"<<stats.cpu_us<<",\"cpu_tick_us\":"<<stats.cpu_tick_us
        <<",\"stage_count\":"<<stats.decomposition_steps
        <<",\"threshold_probes\":"<<stats.threshold_probes<<",\"weighted_calls\":"<<stats.weighted_calls
        <<",\"steps\":[";
    for(size_t k=0;k<stats.steps.size();++k){if(k)std::cout<<',';const auto& s=stats.steps[k];
        std::cout<<"{\"alpha\":"<<s.q<<",\"alpha_scaled\":"<<s.q_scaled
            <<",\"resource_seed_us\":"<<s.resource_seed_us<<",\"resource_chosen_us\":"<<s.resource_chosen_us
            <<",\"eliminated\":"<<s.eliminated<<",\"degree_before\":"<<s.degree_before
            <<",\"degree_after\":"<<s.degree_after<<",\"selected_threshold\":"<<s.selected_threshold
            <<",\"degree_fallback\":"<<(s.degree_fallback?"true":"false")<<",\"cpu_us\":"<<s.cpu_us
            <<",\"threshold_probes\":"<<s.threshold_probes<<",\"weighted_calls\":"<<s.weighted_calls
            <<",\"sampled_thresholds\":";json_array(s.sampled_thresholds);
        std::cout<<",\"sampled_eliminations\":";json_array(s.sampled_eliminations);
        std::cout<<",\"resource_seed\":";json_array(s.resource_seed);
        std::cout<<",\"cycle_costs_us\":";json_array(s.cycle_costs_us);std::cout<<'}';
    }
    std::cout<<']';
}
void permutations(const std::vector<PermutationSet>& stages) {
    std::cout<<",\"permutations\":[";
    for(size_t k=0;k<stages.size();++k){if(k)std::cout<<',';std::cout<<'[';bool first=true;
        for(const auto& edge:stages[k].mp){if(!first)std::cout<<',';first=false;std::cout<<edge.second;}
        std::cout<<']';
    }
    std::cout<<']';
}
// Evaluation-only replay. Capacities are ORIGINAL units, not scaled units.
// The native decomposition supplies the reference padded matrix; no Local,
// dispatch, topology or pipeline timing implementation is copied here.
std::vector<PermutationSet> read_replay(const std::string& path,unsigned n) {
    std::ifstream in(path);std::string token;
    if(!(in>>token))throw std::invalid_argument("cannot read replay stage count");
    auto count=integer(token);
    if(count>uint64_t(n)*n)throw std::invalid_argument("replay stage count exceeds positive-edge bound");
    std::vector<PermutationSet> result;
    for(uint64_t k=0;k<count;++k) {
        if(!(in>>token))throw std::invalid_argument("truncated replay coefficient");
        auto q=integer(token);
        if(!q||q>UINT32_MAX)throw std::invalid_argument("invalid replay coefficient");
        PermutationSet stage(unsigned(q),1,n);std::vector<bool> used(n,false);
        for(unsigned s=0;s<n;++s) {
            if(!(in>>token))throw std::invalid_argument("truncated replay matching");
            auto d=integer(token);
            if(d>=n||used[d])throw std::invalid_argument("replay is not a permutation");
            used[d]=true;stage.mp[s]=unsigned(d);
        }
        result.push_back(stage);
    }
    if(in>>token)throw std::invalid_argument("trailing replay data");
    return result;
}
DecompositionStats install_replay(FastAll2All& app,const std::vector<PermutationSet>& plan,unsigned n) {
    std::vector<uint64_t> residual(size_t(n)*n,0);
    for(const auto& stage:app.p_sets)for(const auto& edge:stage.mp)
        residual[edge.first*n+edge.second]+=stage.get_freq();
    DecompositionStats stats;
    for(const auto& stage:plan) {
        uint64_t minimum=UINT64_MAX;
        for(const auto& edge:stage.mp)minimum=std::min(minimum,residual[edge.first*n+edge.second]);
        // This is execution replay, not greedy BvN generation: reordering a
        // complete decomposition preserves legality even when q < minimum.
        if(stage.get_freq()>minimum)throw std::invalid_argument("replay coefficient exceeds residual capacity");
        DecompositionStepStats step;step.q=step.q_scaled=stage.get_freq();
        step.selected_threshold=step.q;
        for(const auto& edge:stage.mp) {
            auto& value=residual[edge.first*n+edge.second];value-=step.q;
            if(!value)++step.eliminated;
        }
        stats.steps.push_back(step);
    }
    for(auto value:residual)if(value)throw std::invalid_argument("incomplete replay decomposition");
    stats.decomposition_steps=plan.size();app.p_sets=plan;return stats;
}
// Research baseline: GPU cyclic rounds, not NCCL execution. Same-round local
// and network work overlap; successive rounds wait for completion, as in the
// repository's original spread_out_baseline. Only network pricing is replaced.
void spreadout_json(const std::vector<unsigned>& data,unsigned n,unsigned g,
                    unsigned repeats,double mb,bool shared,unsigned profile) {
    struct Round {
        unsigned offset;
        double intra=0,total=0;
        native_topology::GpuStageCost network;
        std::vector<std::vector<uint64_t>> flows;
    };
    unsigned ranks=n*g;
    auto top=experiment_topology(n,g,profile);
    auto intra=get_intra_link_info(H100),inter=get_inter_link_info(ETHER400);
    std::vector<Round> first;std::vector<double> cpu,wall;
    double total=0;
    for(unsigned rep=0;rep<repeats;++rep) {
        auto start=std::chrono::steady_clock::now();auto tick=std::clock();
        std::vector<Round> rounds;double sum=0;
        for(unsigned offset=1;offset<ranks;++offset) {
            Round round;round.offset=offset;
            std::vector<native_topology::GpuTransfer> network;
            for(unsigned src=0;src<ranks;++src) {
                unsigned dst=(src+offset)%ranks,amount=data[src*ranks+dst];
                if(!amount)continue;
                round.flows.push_back({src,dst,amount});
                if(src/g==dst/g)round.intra=std::max(round.intra,intra.alpha+amount*mb*intra.beta);
                else {
                    network.push_back({src,dst,double(amount)*mb*1e6});
                    if(!shared)round.network.total_us=std::max(round.network.total_us,inter.alpha+amount*mb*inter.beta);
                }
            }
            if(shared)round.network=top.evaluate_gpu(network);
            round.total=std::max(round.intra,round.network.total_us);
            sum+=round.total;
            if(!round.flows.empty())rounds.push_back(std::move(round));
        }
        cpu.push_back(1e6*double(std::clock()-tick)/CLOCKS_PER_SEC);
        wall.push_back(std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count());
        if(rep==0){total=sum;first=std::move(rounds);}
        else if(sum!=total)throw std::runtime_error("spreadout repeat changed");
    }
    // Independent legacy alpha-beta reference, never used for shared-WAN ranking.
    auto copy=data;
    double legacy=spread_out_baseline(copy.data(),n,g,inter,intra,mb);
    std::cout<<std::setprecision(17)<<"{\"mode\":\"spreadout\",\"n\":"<<n<<",\"g\":"<<g
        <<",\"topology\":"<<(shared?"true":"false")<<",\"unit_mb\":"<<mb
        <<",\"total_us\":"<<total<<",\"active_rounds\":"<<first.size()
        <<",\"legacy_spreadout_us\":"<<legacy<<",\"simulation_cpu_samples_us\":";json_array(cpu);
    std::cout<<",\"simulation_wall_samples_us\":";json_array(wall);
    std::cout<<",\"resource_names\":[";
    for(unsigned e=0;e<top.resource_count();++e){if(e)std::cout<<',';std::cout<<'"'<<top.resource(e).name<<'"';}
    std::cout<<"],\"rounds\":[";
    for(size_t k=0;k<first.size();++k) {
        if(k)std::cout<<',';auto& r=first[k];
        std::cout<<"{\"offset\":"<<r.offset<<",\"intra_us\":"<<r.intra
            <<",\"network_us\":"<<r.network.total_us<<",\"alpha_us\":"<<r.network.alpha_us
            <<",\"serial_us\":"<<r.network.serial_us<<",\"total_us\":"<<r.total<<",\"resource_bytes\":";
        json_array(r.network.resource_bytes);std::cout<<",\"flows\":[";
        for(size_t j=0;j<r.flows.size();++j){if(j)std::cout<<',';json_array(r.flows[j]);}
        std::cout<<"]}";
    }
    std::cout<<']';
    if(shared){std::cout<<",\"topology_snapshot\":";topology_json(top);}
    std::cout<<"}\n";
}
}
int main(int argc,char** argv) {
    try {
        std::map<std::string,std::string> args;
        for(int i=1;i<argc;++i){std::string key=argv[i];
            if(key=="--help"&&argc==2){std::cout<<"--mode matrix|pipeline|spreadout --n N --g G --strategy 0|1|2|3 --budget B --repeat R --unit-mb X --topology 0|1 --scaling 0|1 (pipeline only) --resource-rounds 0..8 (S1 topology pipeline) --resource-cycle 0|2|3 (same-q local descent) --resource-skip-step K (research intervention, zero-based) --wan-profile 0|1|2 (shared topology only) --replay-plan PATH (evaluation-only pipeline, original capacities then n destinations); matrix values on stdin\n";return 0;}
            if(key!="--mode"&&key!="--n"&&key!="--g"&&key!="--strategy"&&key!="--budget"&&key!="--repeat"&&key!="--topology"&&key!="--unit-mb"&&key!="--scaling"&&key!="--resource-rounds"&&key!="--resource-cycle"&&key!="--resource-skip-step"&&key!="--wan-profile"&&key!="--replay-plan")throw std::invalid_argument("unknown option");
            if(args.count(key)||i+1>=argc)throw std::invalid_argument("duplicate or incomplete option");
            args[key]=argv[++i];
        }
        auto get=[&](const char* key,const char* fallback){auto i=args.find(key);return i==args.end()?std::string(fallback):i->second;};
        auto nn=integer(get("--n","3")),gg=integer(get("--g","4")),ss=integer(get("--strategy","0"));
        auto bb=integer(get("--budget","8")),rr=integer(get("--repeat","3")),tt=integer(get("--topology","0"));
        auto scaling=integer(get("--scaling","0"));
        auto resource_rounds=integer(get("--resource-rounds","0"));
        auto resource_cycle=integer(get("--resource-cycle","0"));
        auto skip=integer(get("--resource-skip-step","4294967295"));
        auto profile=integer(get("--wan-profile","0"));
        std::string mode=get("--mode","matrix");double mb=number(get("--unit-mb","0.001024"));
        const bool replay=args.count("--replay-plan");
        if(replay&&(get("--replay-plan","").empty()||mode!="pipeline"||tt!=1||ss||resource_rounds||resource_cycle||skip!=UINT32_MAX))
            throw std::invalid_argument("replay requires topology pipeline, strategy0 and no refinements");
        if(nn<1||nn>64||gg<1||gg>16||ss>3||bb<1||bb>64||rr<1||rr>100||tt>1||(mode!="matrix"&&mode!="pipeline"&&mode!="spreadout"))throw std::invalid_argument("option range");
        if(mode=="matrix"&&tt)throw std::invalid_argument("topology evaluation requires pipeline mode");
        if(profile>2||(profile&&(!tt||mode=="matrix")))throw std::invalid_argument("WAN profile requires shared topology and 0..2");
        if(scaling>1||(mode!="pipeline"&&scaling))throw std::invalid_argument("scaling requires pipeline mode and flag 0|1");
        if(mode=="spreadout"&&ss)throw std::invalid_argument("spreadout has no BvN strategy");
        if(resource_rounds>8||(resource_rounds&&(mode!="pipeline"||ss!=1||!tt)))
            throw std::invalid_argument("resource rounds require S1 pipeline and topology");
        if(skip>UINT32_MAX||(skip!=UINT32_MAX&&!resource_rounds))
            throw std::invalid_argument("resource skip requires resource pricing and uint32 step");
        if((resource_cycle&&resource_cycle!=2&&resource_cycle!=3)||
           (resource_cycle&&(mode!="pipeline"||ss!=1||!tt||skip!=UINT32_MAX)))
            throw std::invalid_argument("resource cycle requires S1 topology pipeline, length 2|3 and no skip");
        unsigned n=nn,g=gg,r=mode=="matrix"?n:n*g;std::vector<unsigned> data(size_t(r)*r);std::string token;
        const auto replay_plan=replay?read_replay(get("--replay-plan",""),n):std::vector<PermutationSet>();
        for(auto& x:data){if(!(std::cin>>token))throw std::invalid_argument("truncated input");auto v=integer(token);if(v>INT32_MAX)throw std::overflow_error("input cell exceeds INT32_MAX");x=v;}
        if(std::cin>>token)throw std::invalid_argument("trailing input");
        if(mode!="matrix")for(unsigned i=0;i<r;++i)if(data[i*r+i])throw std::invalid_argument("GPU diagonal must be zero");
        // 原生矩阵求和使用uint；在进入原生代码之前防止聚合溢出。
        for(unsigned s=0;s<n;++s){uint64_t row=0,col=0;
            unsigned count=mode=="matrix"?1:g;
            for(unsigned u=0;u<count;++u)for(unsigned d=0;d<r;++d){row+=data[(s*count+u)*r+d];col+=data[d*r+s*count+u];}
            if(row>UINT32_MAX-r||col>UINT32_MAX-r)throw std::overflow_error("native aggregate overflow");
        }
        if(mode=="spreadout"){spreadout_json(data,n,g,rr,mb,tt!=0,profile);return 0;}
        DecompositionOptions options;options.strategy=static_cast<DecompositionStrategy>(ss);options.threshold_budget=bb;
        options.resource_rounds=resource_rounds;
        options.resource_cycle=resource_cycle;
        options.resource_skip_step=skip;
        std::vector<double> cpu_samples,wall_samples;DecompositionStats first_stats;
        std::vector<double> padding_samples,planning_samples;
        std::vector<PermutationSet> first_stages;double total=0,balance=0,intrinsic=0,ratio=0;
        std::vector<double> dispatch;std::vector<std::vector<uint64_t>> payload;
        std::vector<double> network_stages,resource_work_ratio;
        std::vector<std::string> resource_names;
        for(unsigned rep=0;rep<rr;++rep){auto start=std::chrono::steady_clock::now();DecompositionStats current;
            std::vector<PermutationSet> stages;
            if(mode=="matrix"){
                Matrix matrix(data.data(),n);FastAll2All app(&matrix,1,ETHER400,H100,mb);
                auto padding_start=std::chrono::steady_clock::now();
                app.to_scaled_doubly_stochastic_matrix();
                padding_samples.push_back(std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-padding_start).count());
                app.decompose(options,&current);
                double wall=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count();wall_samples.push_back(wall);
                planning_samples.push_back(wall);
                if(!app.verify_decomposition())throw std::runtime_error("matrix reconstruction failed");
                stages=app.p_sets;
            } else {
                std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
                for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
                GlobalScheduler scheduler(n,g,locals,ETHER400,H100,scaling!=0,mb);
                if(!replay)scheduler.set_decomposition(options);
                auto top=experiment_topology(n,g,profile);
                if(tt&&!replay)scheduler.set_schedule(ScheduleMode::Native,&top);
                pipeline_hooks_t hooks;
                if(replay) {
                    hooks.prepare=[&](Matrix&,const std::vector<uint64_t>&,FastAll2All& app) {
                        current=install_replay(app,replay_plan,n);
                    };
                    hooks.communication=[&](const PermutationSet& stage,const std::vector<uint64_t>& actual) {
                        std::vector<native_topology::GpuTransfer> flows;
                        for(unsigned s=0;s<n;++s)if(s!=stage.mp.at(s))for(unsigned u=0;u<g;++u)
                            if(actual[s*g+u])flows.push_back({s*g+u,stage.mp.at(s)*g+u,double(actual[s*g+u])*mb*1e6});
                        return top.evaluate_gpu(flows).total_us;
                    };
                }
                auto result=scheduler.pipeline2(mb,&balance_alpha_beta,&hooks);
                padding_samples.push_back(hooks.padding_wall_us);planning_samples.push_back(hooks.planning_us);
                wall_samples.push_back(std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count());
                if(!std::isfinite(result.t))throw std::runtime_error("nonfinite pipeline time");
                if(result.t==0)result.ratio=0;
                else if(!std::isfinite(result.ratio))throw std::runtime_error("nonfinite pipeline overlap ratio");
                if(!replay)current=scheduler.decomposition_stats();stages=hooks.stages;
                if(rep==0){total=result.t;ratio=result.ratio;balance=hooks.balance_us;intrinsic=hooks.intrinsic_us;dispatch=hooks.dispatch_us;payload=hooks.payload_units;
                    std::vector<double> resource_bytes(top.resource_count(),0);
                    for(size_t k=0;k<stages.size();++k){
                        if(!tt){network_stages.push_back(scheduler.permutation_set_cost(stages[k].get_freq(),mb));continue;}
                        std::vector<native_topology::GpuTransfer> flows;
                        for(unsigned s=0;s<n;++s)if(s!=stages[k].mp.at(s))for(unsigned u=0;u<g;++u)
                            if(payload[k][s*g+u])flows.push_back({s*g+u,stages[k].mp.at(s)*g+u,double(payload[k][s*g+u])*mb*1e6});
                        auto cost=top.evaluate_gpu(flows);network_stages.push_back(cost.total_us);
                        for(size_t e=0;e<resource_bytes.size();++e)resource_bytes[e]+=cost.resource_bytes[e];
                    }
                    if(tt)for(size_t e=0;e<resource_bytes.size();++e){
                        resource_names.push_back(top.resource(e).name);
                        resource_work_ratio.push_back(total>0?.008*resource_bytes[e]/top.resource(e).bandwidth_gbps/total:0);
                    }
                }
            }
            cpu_samples.push_back(current.cpu_us);
            if(rep==0){first_stats=current;first_stages=stages;}
            else {if(stages.size()!=first_stages.size())throw std::runtime_error("repeat stage count changed");
                for(size_t k=0;k<stages.size();++k)if(stages[k].mp!=first_stages[k].mp||stages[k].get_freq()!=first_stages[k].get_freq())throw std::runtime_error("repeat plan changed");}
        }
        std::cout<<std::setprecision(17)<<"{\"mode\":\""<<mode<<"\",\"strategy\":"<<ss<<",\"budget\":"<<bb<<",\"n\":"<<n<<",\"g\":"<<(mode=="matrix"?1:g)<<",\"unit_mb\":"<<mb<<",\"topology\":"<<(tt?"true":"false")<<',';
        std::cout<<"\"scaling_enabled\":"<<(scaling?"true":"false")<<',';
        if(replay)std::cout<<"\"replay_only\":true,\"replay_step_units\":\"original\",";
        std::cout<<"\"resource_rounds\":"<<resource_rounds<<",\"resource_cycle\":"<<resource_cycle<<',';
        stats_json(first_stats);std::cout<<",\"cpu_samples_us\":";json_array(cpu_samples);std::cout<<",\"setup_and_execution_wall_samples_us\":";json_array(wall_samples);permutations(first_stages);
        std::cout<<",\"padding_wall_samples_us\":";json_array(padding_samples);
        std::cout<<",\"planning_wall_samples_us\":";json_array(planning_samples);
        if(mode=="pipeline"){
            if(tt){std::cout<<",\"topology_snapshot\":";topology_json(experiment_topology(n,g,profile));}
            std::cout<<",\"total_us\":"<<total<<",\"balance_us\":"<<balance<<",\"intrinsic_us\":"<<intrinsic<<",\"unhidden_intra_ratio\":"<<ratio<<",\"dispatch_us\":";json_array(dispatch);
            std::cout<<",\"payload_units\":[";for(size_t k=0;k<payload.size();++k){if(k)std::cout<<',';json_array(payload[k]);}std::cout<<']';
            std::cout<<",\"network_stages_us\":";json_array(network_stages);
            std::cout<<",\"resource_capacity_work_fraction\":";json_array(resource_work_ratio);
            std::cout<<",\"resource_names\":[";for(size_t e=0;e<resource_names.size();++e){if(e)std::cout<<',';std::cout<<'"'<<resource_names[e]<<'"';}std::cout<<']';
        }
        std::cout<<"}\n";return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
