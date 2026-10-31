#include "global.h"
#include "matching.h"
#include "test.h"
#include <chrono>
#include <iomanip>
#include <limits>
#include <memory>
#include <set>
#include <stdexcept>

using namespace native_topology;
using Clock=std::chrono::steady_clock;
static double elapsed(Clock::time_point start){return std::chrono::duration<double,std::micro>(Clock::now()-start).count();}
static uint64_t integer(const std::string& v) {
  if(v.empty()||v.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("expected unsigned integer");
  return std::stoull(v);
}
template<class V> static void numbers(const V& v){std::cout<<"[";bool comma=false;for(auto x:v){if(comma)std::cout<<",";comma=true;std::cout<<x;}std::cout<<"]";}
static void dict(const std::map<std::string,uint64_t>& values){std::cout<<"{";bool comma=false;for(auto v:values){if(comma)std::cout<<",";comma=true;std::cout<<"\""<<v.first<<"\":"<<v.second;}std::cout<<"}";}
static void capacities(const std::map<std::string,double>& values){std::cout<<"{";bool comma=false;for(auto v:values){if(comma)std::cout<<",";comma=true;std::cout<<"\""<<v.first<<"\":"<<v.second;}std::cout<<"}";}
struct Record {unsigned freq=0;std::vector<unsigned> map;std::vector<uint64_t> payload;Cost cost;double local_us=0;};

int main(int argc,char** argv) {
  try {
    if(argc==4&&std::string(argv[1])=="--zipf") {
      auto seed=integer(argv[2]),count=integer(argv[3]);
      if(seed>UINT32_MAX||count>1048576)throw std::invalid_argument("generator range");
      zipf_distribution generator(.9);std::srand(seed);std::vector<unsigned> weights;
      generator.zipf(&weights,count);numbers(weights);std::cout<<"\n";return 0;
    }
    if(argc!=4&&argc!=5)throw std::runtime_error("usage: native_topology servers GPUs mode [WAN_oneway_us]");
    auto nn=integer(argv[1]),gg=integer(argv[2]);
    if(!nn||nn>64||!gg||gg>16)throw std::runtime_error("unsupported dimensions");
    unsigned n=nn,g=gg,r=n*g;std::string mode=argv[3];
    std::set<std::string> allowed={"legacy","legacy-hook","native","homogeneous","wan-repair","link-cost","link-cost-wan","maxmin","spreadout"};
    if(!allowed.count(mode))throw std::invalid_argument("unknown mode");
    double latency=5000;if(argc==5){std::string v=argv[4];size_t used=0;latency=std::stod(v,&used);if(used!=v.size())throw std::invalid_argument("invalid latency");}
    std::vector<unsigned> data(r*r);std::string token;
    for(auto& v:data){if(!(std::cin>>token))throw std::runtime_error("matrix truncated");auto value=integer(token);
      if(value>INT32_MAX)throw std::overflow_error("matrix cell exceeds native signed intermediate limit");v=value;}
    if(std::cin>>token)throw std::invalid_argument("trailing matrix input");
    for(unsigned s=0;s<r;++s)if(data[s*r+s])throw std::invalid_argument("nonzero diagonal");
    for(unsigned s=0;s<n;++s){uint64_t row=0,col=0;for(unsigned i=0;i<g;++i)for(unsigned j=0;j<r;++j){row+=data[(s*g+i)*r+j];col+=data[j*r+s*g+i];}
      if(row>UINT32_MAX-n*g||col>UINT32_MAX-n*g)throw std::overflow_error("native server row/column range exceeded");}
    std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=std::min(2U,s*3/n);
    Topology top(n,g,dc,400,400,100,latency);
    if(mode=="homogeneous")top=Topology::homogeneous(n,g,22,.7);
    auto start=Clock::now();std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
    GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
    pipeline_hooks_t hooks;std::vector<uint64_t> pending,service;std::unique_ptr<Planner> planner;
    double extra_planner_us=0;std::vector<Record> records;
    hooks.prepare=[&](Matrix& matrix,const std::vector<uint64_t>& rows,FastAll2All& app){
      pending=rows;service.resize(n*n);for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)service[s*n+d]=matrix.get(s,d);
      if(mode=="wan-repair"||mode=="link-cost"||mode=="link-cost-wan"||mode=="maxmin"){
        auto time=Clock::now();planner.reset(new Planner(top,rows,mode));app.p_sets=planner->decompose(matrix);extra_planner_us=elapsed(time);
      }
    };
    hooks.communication=[&](const PermutationSet& p,const std::vector<uint64_t>& actual){
      Record record;record.freq=p.get_freq();record.payload=actual;std::vector<Transfer> transfers;std::set<unsigned> destinations;
      for(unsigned s=0;s<n;++s){unsigned d=p.mp.at(s);if(d>=n||!destinations.insert(d).second)throw std::runtime_error("not a perfect matching");record.map.push_back(d);uint64_t bytes=0;
        for(unsigned i=0;i<g;++i){auto& left=pending[(s*n+d)*g+i];uint64_t sent=std::min<uint64_t>(p.get_freq(),left);
          if(sent!=actual[s*g+i])throw std::runtime_error("planner shadow/native dispatch mismatch");left-=sent;bytes+=sent;}
        transfers.push_back({s,d,bytes});
      }
      record.cost=top.evaluate(transfers);records.push_back(record);
      return mode=="legacy-hook"?scheduler.permutation_set_cost(p.get_freq(),1e-6):record.cost.total_us;
    };
    bool legacy=mode=="legacy";auto result=mode=="spreadout"?pipeline_result_t{0,0}:scheduler.pipeline2(1e-6,&balance_alpha_beta,legacy?nullptr:&hooks);
    if(!legacy&&mode!="spreadout")for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)for(unsigned i=0;i<g;++i)
      if(pending[(s*n+d)*g+i])throw std::runtime_error("native dispatch left real inter-server data");
    uint64_t inter_bytes=0;for(unsigned s=0;s<r;++s)for(unsigned d=0;d<r;++d)if(s/g!=d/g)inter_bytes+=data[s*r+d];
    double total_us=result.t;
    if(mode=="spreadout") {
      records.clear();total_us=0;auto intra=get_intra_link_info(H100);
      for(unsigned offset=0;offset<r;++offset){Record rec;rec.map.resize(r);rec.payload.resize(r);std::vector<Transfer> transfers;
        for(unsigned s=0;s<r;++s){unsigned d=(s+offset)%r;auto bytes=data[s*r+d];rec.map[s]=d;rec.payload[s]=bytes;
          if(!bytes)continue;
          if(s/g==d/g)rec.local_us=std::max(rec.local_us,intra.alpha+bytes*intra.beta*1e-6);
          else transfers.push_back({s/g,d/g,bytes});
        }
        rec.cost=top.evaluate(transfers);total_us+=std::max(rec.cost.total_us,rec.local_us);records.push_back(rec);
      }
    }
    double wall_us=elapsed(start);
    std::cout<<std::setprecision(17)<<"{\"mode\":\""<<mode<<"\",\"total_us\":"<<total_us<<",\"legacy_spreadout_us\":"
      <<spread_out_baseline(data.data(),n,g,get_inter_link_info(ETHER400),get_intra_link_info(H100),1e-6)
      <<",\"balance_us\":"<<hooks.balance_us<<",\"intrinsic_us\":"<<hooks.intrinsic_us<<",\"native_decompose_us\":"<<hooks.native_decompose_us
      <<",\"extra_planner_us\":"<<extra_planner_us<<",\"wall_us\":"<<wall_us<<",\"inter_bytes\":"<<inter_bytes
      <<",\"payload_verified\":"<<(legacy?"false":"true")<<",\"cycle_trials\":"<<(planner?planner->cycle_trials:0)
      <<",\"changed_matches\":"<<(planner?planner->changed_matches:0)<<",\"repairs\":"<<(planner?planner->repair_matches:0)
      <<",\"service\":";numbers(service);std::cout<<",\"dispatch_us\":";numbers(hooks.dispatch_us);
    std::cout<<",\"stages\":[";
    for(size_t k=0;k<records.size();++k){const auto& rec=records[k];if(k)std::cout<<",";
      std::cout<<"{\"freq\":"<<rec.freq<<",\"map\":";numbers(rec.map);std::cout<<",\"payload\":";numbers(rec.payload);
      std::cout<<",\"alpha_us\":"<<rec.cost.alpha_us<<",\"serial_us\":"<<rec.cost.serial_us<<",\"communication_us\":"<<rec.cost.total_us
        <<",\"local_us\":"<<rec.local_us<<",\"links\":";dict(rec.cost.bytes);std::cout<<",\"bandwidths\":";capacities(rec.cost.bandwidth_gbps);std::cout<<"}";
    }
    std::cout<<"]}\n";
  } catch(const std::exception& e) {std::cerr<<e.what()<<"\n";return 1;}
}
