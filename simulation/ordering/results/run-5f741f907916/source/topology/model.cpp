#include "model.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace native_topology {
static void positive(double v){if(!std::isfinite(v)||v<=0)throw std::invalid_argument("bandwidth must be finite and positive");}
Topology::Topology(unsigned n_,unsigned g_,std::vector<int> dc_,double sb,double gb,double wb,double latency)
 :n(n_),g(g_),dc(std::move(dc_)),routes(n*n),alphas(n*n,0) {
  if(!n||n>64||!g||g>16||dc.size()!=n)throw std::invalid_argument("invalid topology dimensions");
  positive(sb);positive(gb);positive(wb);
  if(!std::isfinite(latency)||latency<0)throw std::invalid_argument("invalid one-way WAN latency");
  for(int d:dc)if(d<0||d>=int(n))throw std::invalid_argument("invalid DC label");
  for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d) {
    if(s==d)continue;
    auto& route=routes[s*n+d];
    auto add=[&](std::string key,double bw){route.push_back(key);bandwidths[key]=bw;};
    add("server-out:"+std::to_string(s),sb);add("server-in:"+std::to_string(d),sb);
    if(dc[s]==dc[d])alphas[s*n+d]=1.4;
    else {
      add("gateway-out:"+std::to_string(dc[s]),gb);add("gateway-in:"+std::to_string(dc[d]),gb);
      add("wan:"+std::to_string(dc[s])+":"+std::to_string(dc[d]),wb);
      alphas[s*n+d]=latency+2.8;
    }
  }
}
Topology Topology::homogeneous(unsigned n,unsigned g,double beta,double alpha) {
  positive(beta);if(!std::isfinite(alpha)||alpha<0)throw std::invalid_argument("invalid alpha");
  Topology result(n,g,std::vector<int>(n,0));result.bandwidths.clear();
  for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d) {
    result.alphas[s*n+d]=alpha;
    for(const auto& key:result.routes[s*n+d])result.bandwidths[key]=g*8000./beta;
  }
  return result;
}
double Topology::path_alpha(unsigned s,unsigned d)const {
  if(s>=n||d>=n)throw std::out_of_range("route endpoint");return alphas[s*n+d];
}
std::vector<std::string> Topology::path(unsigned s,unsigned d)const {
  if(s>=n||d>=n)throw std::out_of_range("route endpoint");return routes[s*n+d];
}
Cost Topology::evaluate(const std::vector<Transfer>& transfers)const {
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
}
