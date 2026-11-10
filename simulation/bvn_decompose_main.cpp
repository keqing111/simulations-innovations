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
void stats_json(const DecompositionStats& stats) {
    std::cout<<"\"cpu_us\":"<<stats.cpu_us<<",\"cpu_tick_us\":"<<stats.cpu_tick_us
        <<",\"stage_count\":"<<stats.decomposition_steps
        <<",\"threshold_probes\":"<<stats.threshold_probes<<",\"weighted_calls\":"<<stats.weighted_calls
        <<",\"steps\":[";
    for(size_t k=0;k<stats.steps.size();++k){if(k)std::cout<<',';const auto& s=stats.steps[k];
        std::cout<<"{\"alpha\":"<<s.q<<",\"alpha_scaled\":"<<s.q_scaled
            <<",\"eliminated\":"<<s.eliminated<<",\"degree_before\":"<<s.degree_before
            <<",\"degree_after\":"<<s.degree_after<<",\"selected_threshold\":"<<s.selected_threshold
            <<",\"degree_fallback\":"<<(s.degree_fallback?"true":"false")<<",\"cpu_us\":"<<s.cpu_us
            <<",\"threshold_probes\":"<<s.threshold_probes<<",\"weighted_calls\":"<<s.weighted_calls
            <<",\"sampled_thresholds\":";json_array(s.sampled_thresholds);
        std::cout<<",\"sampled_eliminations\":";json_array(s.sampled_eliminations);std::cout<<'}';
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
}
int main(int argc,char** argv) {
    try {
        std::map<std::string,std::string> args;
        for(int i=1;i<argc;++i){std::string key=argv[i];
            if(key=="--help"&&argc==2){std::cout<<"--mode matrix|pipeline --n N --g G --strategy 0|1|2|3 --budget B --repeat R --unit-mb X --topology 0|1; matrix values on stdin\n";return 0;}
            if(key!="--mode"&&key!="--n"&&key!="--g"&&key!="--strategy"&&key!="--budget"&&key!="--repeat"&&key!="--unit-mb"&&key!="--topology")throw std::invalid_argument("unknown option");
            if(args.count(key)||i+1>=argc)throw std::invalid_argument("duplicate or incomplete option");
            args[key]=argv[++i];
        }
        auto get=[&](const char* key,const char* fallback){auto i=args.find(key);return i==args.end()?std::string(fallback):i->second;};
        auto nn=integer(get("--n","3")),gg=integer(get("--g","4")),ss=integer(get("--strategy","0"));
        auto bb=integer(get("--budget","8")),rr=integer(get("--repeat","3")),tt=integer(get("--topology","0"));
        std::string mode=get("--mode","matrix");double mb=number(get("--unit-mb","0.001024"));
        if(nn<1||nn>64||gg<1||gg>16||ss>3||bb<1||bb>64||rr<1||rr>100||tt>1||(mode!="matrix"&&mode!="pipeline"))throw std::invalid_argument("option range");
        if(mode=="matrix"&&tt)throw std::invalid_argument("topology evaluation requires pipeline mode");
        unsigned n=nn,g=gg,r=mode=="matrix"?n:n*g;std::vector<unsigned> data(size_t(r)*r);std::string token;
        for(auto& x:data){if(!(std::cin>>token))throw std::invalid_argument("truncated input");auto v=integer(token);if(v>INT32_MAX)throw std::overflow_error("input cell exceeds INT32_MAX");x=v;}
        if(std::cin>>token)throw std::invalid_argument("trailing input");
        if(mode=="pipeline")for(unsigned i=0;i<r;++i)if(data[i*r+i])throw std::invalid_argument("GPU diagonal must be zero");
        // 原生矩阵求和使用uint；在进入原生代码之前防止聚合溢出。
        for(unsigned s=0;s<n;++s){uint64_t row=0,col=0;
            unsigned count=mode=="matrix"?1:g;
            for(unsigned u=0;u<count;++u)for(unsigned d=0;d<r;++d){row+=data[(s*count+u)*r+d];col+=data[d*r+s*count+u];}
            if(row>UINT32_MAX-r||col>UINT32_MAX-r)throw std::overflow_error("native aggregate overflow");
        }
        DecompositionOptions options;options.strategy=static_cast<DecompositionStrategy>(ss);options.threshold_budget=bb;
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
                GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,mb);scheduler.set_decomposition(options);
                std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
                native_topology::Topology top(n,g,dc,400,400,100,50);
                if(tt)scheduler.set_schedule(ScheduleMode::Native,&top);
                pipeline_hooks_t hooks;auto result=scheduler.pipeline2(mb,&balance_alpha_beta,&hooks);
                padding_samples.push_back(hooks.padding_wall_us);planning_samples.push_back(hooks.planning_us);
                wall_samples.push_back(std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count());
                if(!std::isfinite(result.t))throw std::runtime_error("nonfinite pipeline time");
                if(result.t==0)result.ratio=0;
                else if(!std::isfinite(result.ratio))throw std::runtime_error("nonfinite pipeline overlap ratio");
                current=scheduler.decomposition_stats();stages=hooks.stages;
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
        stats_json(first_stats);std::cout<<",\"cpu_samples_us\":";json_array(cpu_samples);std::cout<<",\"setup_and_execution_wall_samples_us\":";json_array(wall_samples);permutations(first_stages);
        std::cout<<",\"padding_wall_samples_us\":";json_array(padding_samples);
        std::cout<<",\"planning_wall_samples_us\":";json_array(planning_samples);
        if(mode=="pipeline"){
            std::cout<<",\"total_us\":"<<total<<",\"balance_us\":"<<balance<<",\"intrinsic_us\":"<<intrinsic<<",\"unhidden_intra_ratio\":"<<ratio<<",\"dispatch_us\":";json_array(dispatch);
            std::cout<<",\"payload_units\":[";for(size_t k=0;k<payload.size();++k){if(k)std::cout<<',';json_array(payload[k]);}std::cout<<']';
            std::cout<<",\"network_stages_us\":";json_array(network_stages);
            std::cout<<",\"resource_capacity_work_fraction\":";json_array(resource_work_ratio);
            std::cout<<",\"resource_names\":[";for(size_t e=0;e<resource_names.size();++e){if(e)std::cout<<',';std::cout<<'"'<<resource_names[e]<<'"';}std::cout<<']';
        }
        std::cout<<"}\n";return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
