// ============================================================================
// 资源模型的构造与评估。
//
// 每条 server 对路径都按固定顺序展开成 2~5 个有向资源：
//   同 DC : server-out(src) -> server-in(dst)
//   跨 DC : server-out(src) -> gateway-out(src DC) -> wan(src: dst)
//           -> gateway-in(dst DC) -> server-in(dst)
// 资源名称即 map 键，因此路径上的共享关系直接体现为同名 key 的字节累加。
// 单位换算：字节 * 8 bit / (Gbps * 1e9) 秒 = 0.008 * bytes / Gbps 微秒。
// ============================================================================
#include "model.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace native_topology {
// 带宽必须是有限正数；延迟允许为 0，但不允许负值或 NaN。
static void positive(double v){if(!std::isfinite(v)||v<=0)throw std::invalid_argument("bandwidth must be finite and positive");}
Topology::Topology(unsigned n_,unsigned g_):n(n_),g(g_),dc(n_),routes(n_*n_),alphas(n_*n_,0) {
  if(!n||n>64||!g||g>16)throw std::invalid_argument("invalid topology dimensions");
}
// 构造物理拓扑并预展开全部路径。限制 n<=64、g<=16 与核心仿真器保持一致；
// DC 标签只要求落在 [0,n)，不要求连续或均衡。
Topology::Topology(unsigned n_,unsigned g_,std::vector<int> dc_,double sb,double gb,double wb,double latency)
 :n(n_),g(g_),dc(std::move(dc_)),routes(n*n),alphas(n*n,0) {
  if(!n||n>64||!g||g>16||dc.size()!=n)throw std::invalid_argument("invalid topology dimensions");
  positive(sb);positive(gb);positive(wb);
  if(!std::isfinite(latency)||latency<0)throw std::invalid_argument("invalid one-way WAN latency");
  for(int d:dc)if(d<0||d>=int(n))throw std::invalid_argument("invalid DC label");
  for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d) {
    if(s==d)continue;
    auto& route=routes[s*n+d];
    // add 同时登记资源带宽；同一资源被多条路径引用时带宽一致（同构假设）。
    auto add=[&](std::string key,double bw){route.push_back(key);bandwidths[key]=bw;};
    add("server-out:"+std::to_string(s),sb);add("server-in:"+std::to_string(d),sb);
    if(dc[s]==dc[d])alphas[s*n+d]=1.4;
    else {
      add("gateway-out:"+std::to_string(dc[s]),gb);add("gateway-in:"+std::to_string(dc[d]),gb);
      // WAN 资源按方向区分：dcs -> dcd 与 dcd -> dcs 是两条独立链路。
      add("wan:"+std::to_string(dc[s])+":"+std::to_string(dc[d]),wb);
      alphas[s*n+d]=latency+2.8;
    }
  }
  cache_legacy_paths();
}
// 均匀解析模型：忽略 DC/路径差异，所有资源带宽统一换算成
// g*8000/beta Gbps，使“一个 server 的所有 GPU 同时发 1MB”恰好耗时 beta。
// 主要用于给出与拓扑无关的理想下界。
Topology Topology::homogeneous(unsigned n,unsigned g,double beta,double alpha) {
  positive(beta);if(!std::isfinite(alpha)||alpha<0)throw std::invalid_argument("invalid alpha");
  Topology result(n,g,std::vector<int>(n,0));result.bandwidths.clear();
  for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d) {
    result.alphas[s*n+d]=alpha;
    for(const auto& key:result.routes[s*n+d])result.bandwidths[key]=g*8000./beta;
  }
  result.cache_legacy_paths();
  return result;
}
// 查询接口；越界端点统一抛 out_of_range，调用方（Planner）假定输入合法。
double Topology::path_alpha(unsigned s,unsigned d)const {
  if(!legacy_server_view_)throw std::logic_error("GPU-only topology: use gpu_path");
  if(s>=n||d>=n)throw std::out_of_range("route endpoint");return alphas[s*n+d];
}
std::vector<std::string> Topology::path(unsigned s,unsigned d)const {
  if(!legacy_server_view_)throw std::logic_error("GPU-only topology: use gpu_path");
  if(s>=n||d>=n)throw std::out_of_range("route endpoint");return routes[s*n+d];
}
// 评估一组 server 间流量。步骤：
//   1. 过滤零字节/本地流；
//   2. alpha_us 取所有路径固定延迟的最大值（所有流并行开始，见模型假设）；
//   3. 每条路径上的资源字节累加，检测 uint64 溢出；
//   4. serial_us = max_resource(bytes / bandwidth)，即瓶颈资源的串行时间；
//   5. total = alpha + serial；没有任何流量时 total 保持 0。
// 注意流之间不排队：同一资源只串行，跨资源完全并行。
Cost Topology::evaluate(const std::vector<Transfer>& transfers)const {
  if(!legacy_server_view_)throw std::logic_error("GPU-only topology: use evaluate_gpu");
  Cost result;
  for(const auto& f:transfers) {
    if(f.src>=n||f.dst>=n)throw std::out_of_range("transfer endpoint");
    if(!f.bytes||f.src==f.dst)continue;
    result.alpha_us=std::max(result.alpha_us,alphas[f.src*n+f.dst]);
    for(const auto& key:routes[f.src*n+f.dst]) {
      if(result.bytes[key]>std::numeric_limits<uint64_t>::max()-f.bytes)throw std::overflow_error("link bytes overflow");
      result.bytes[key]+=f.bytes;result.bandwidth_gbps[key]=bandwidths.at(key);
    }
  }
  for(const auto& item:result.bytes)
    result.serial_us=std::max(result.serial_us,.008*double(item.second)/bandwidths.at(item.first));
  if(!result.bytes.empty())result.total_us=result.alpha_us+result.serial_us;
  return result;
}

void Topology::cache_legacy_paths() {
  resources_.clear();
  std::map<std::string,unsigned> ids;
  for(const auto& item:bandwidths) {
    ids[item.first]=resources_.size();resources_.push_back({item.first,item.second});
  }
  unsigned ranks=n*g;
  gpu_paths_.assign(ranks*ranks,PathInfo());
  for(unsigned s=0;s<ranks;++s)for(unsigned d=0;d<ranks;++d)if(s/g!=d/g) {
    auto& p=gpu_paths_[s*ranks+d];
    p.propagation_us=alphas[(s/g)*n+d/g];p.rtt_us=2*p.propagation_us;
    for(const auto& name:routes[(s/g)*n+d/g])p.resource_ids.push_back(ids.at(name));
  }
  validate_gpu_paths();
}
void Topology::validate_gpu_paths() {
  unsigned ranks=n*g;
  if(gpu_paths_.size()!=size_t(ranks)*ranks)throw std::invalid_argument("GPU path dimensions");
  for(const auto& r:resources_)positive(r.bandwidth_gbps);
  path_min_bandwidth_gbps_.assign(gpu_paths_.size(),std::numeric_limits<double>::infinity());
  for(size_t i=0;i<gpu_paths_.size();++i) {
    const auto& p=gpu_paths_[i];
    if(!std::isfinite(p.propagation_us)||p.propagation_us<0||!std::isfinite(p.rtt_us)||p.rtt_us<0)
      throw std::invalid_argument("invalid path latency");
    std::vector<unsigned> seen=p.resource_ids;std::sort(seen.begin(),seen.end());
    if(std::adjacent_find(seen.begin(),seen.end())!=seen.end())throw std::invalid_argument("repeated path resource");
    for(unsigned e:p.resource_ids) {
      if(e>=resources_.size())throw std::invalid_argument("path resource range");
      path_min_bandwidth_gbps_[i]=std::min(path_min_bandwidth_gbps_[i],resources_[e].bandwidth_gbps);
    }
  }
}
Topology Topology::from_fixed_routes(unsigned n,unsigned g,const std::vector<ResourceInfo>& resources,
                                    const std::vector<PathInfo>& paths) {
  Topology result(n,g);result.legacy_server_view_=false;
  result.resources_=resources;result.gpu_paths_=paths;result.validate_gpu_paths();return result;
}
const PathInfo& Topology::gpu_path(unsigned s,unsigned d)const {
  if(s>=n*g||d>=n*g)throw std::out_of_range("GPU endpoint");
  return gpu_paths_[s*(n*g)+d];
}
const ResourceInfo& Topology::resource(unsigned id)const {return resources_.at(id);}
double Topology::path_min_bandwidth_gbps(unsigned s,unsigned d)const {
  gpu_path(s,d);return path_min_bandwidth_gbps_[s*(n*g)+d];
}
GpuStageCost Topology::evaluate_gpu(const std::vector<GpuTransfer>& transfers)const {
  GpuStageCost cost;cost.resource_bytes.assign(resources_.size(),0);
  for(const auto& f:transfers) {
    const auto& p=gpu_path(f.src_gpu,f.dst_gpu);
    if(!std::isfinite(f.bytes)||f.bytes<0)throw std::invalid_argument("invalid flow bytes");
    if(!f.bytes||f.src_gpu/g==f.dst_gpu/g)continue;
    if(p.resource_ids.empty())throw std::invalid_argument("missing active GPU route");
    cost.alpha_us=std::max(cost.alpha_us,p.propagation_us);
    for(unsigned e:p.resource_ids) {
      cost.resource_bytes[e]+=f.bytes;
      if(!std::isfinite(cost.resource_bytes[e]))throw std::overflow_error("resource bytes overflow");
    }
  }
  for(unsigned e=0;e<resources_.size();++e)
    cost.serial_us=std::max(cost.serial_us,.008*cost.resource_bytes[e]/resources_[e].bandwidth_gbps);
  cost.total_us=cost.alpha_us+cost.serial_us;
  if(!std::isfinite(cost.total_us))throw std::overflow_error("network cost overflow");
  return cost;
}
}
