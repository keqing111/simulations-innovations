// Shared CPU preprocessing is external. Native decompose calls original FAST;
// other candidates reuse its Matrix completion and support/augmenting-path order.
#include "all2all.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <unordered_set>
#include <vector>

namespace {
struct Stage { uint32_t weight; std::vector<int> map; };
using Plan=std::vector<Stage>;
using U64=uint64_t;

std::vector<uint32_t> complete(const std::vector<uint32_t>& input,int n,U64& total) {
  Matrix m(const_cast<uint32_t*>(input.data()),n);m.get_sdsm_info();
  auto info=m.sdsm_info;total=info.max_row_col_sum;
  if(!info.is_sdsm) {
    for(auto& row:info.non_max_row) for(auto& col:info.non_max_col) {
      if(col.sum==total)continue;
      uint32_t delta=total-std::max(row.sum,col.sum);
      m.add(delta,row.idx,col.idx);row.sum+=delta;col.sum+=delta;
      if(row.sum==total)break;
    }
  }
  std::vector<uint32_t> result(n*n);
  for(int i=0;i<n;++i)for(int j=0;j<n;++j)result[i*n+j]=m.get(i,j);
  return result;
}

// LAN support is block diagonal. Complete each block to the SAME global target;
// its row/column deficits have equal totals, so greedy in-block padding suffices.
// In particular, virtual LAN slots must never consume real WAN contents later.
std::vector<uint32_t> complete_lan(const std::vector<uint32_t>& input,int n,const int32_t* dc) {
  auto result=input;std::vector<U64> rows(n),cols(n);U64 target=0;
  for(int s=0;s<n;++s)for(int d=0;d<n;++d){rows[s]+=input[s*n+d];cols[d]+=input[s*n+d];}
  for(int s=0;s<n;++s)target=std::max(target,std::max(rows[s],cols[s]));
  for(int s=0;s<n;++s)for(int d=0;d<n;++d)if(dc[s]==dc[d]) {
    U64 amount=std::min(target-rows[s],target-cols[d]);
    result[s*n+d]+=amount;rows[s]+=amount;cols[d]+=amount;
  }
  for(int s=0;s<n;++s)if(rows[s]!=target || cols[s]!=target)
    throw std::runtime_error("LAN block completion failed");
  return result;
}

bool matching(const std::vector<std::vector<int>>& support,std::vector<int>& out,U64& calls) {
  ++calls;int n=support.size();out.assign(n,-1);
  std::vector<int> inverse(n,-1);std::vector<uint8_t> seen(n);
  auto dfs=[&](auto&& self,int row)->bool {
    for(int col:support[row]) if(!seen[col]) {
      seen[col]=1;
      if(inverse[col]<0 || self(self,inverse[col])) {out[row]=col;inverse[col]=row;return true;}
    }
    return false;
  };
  for(int row=0;row<n;++row) {std::fill(seen.begin(),seen.end(),0);if(!dfs(dfs,row))return false;}
  return true;
}

Plan maxmin(const std::vector<uint32_t>& input,int n,U64& calls) {
  U64 total;auto residual=complete(input,n,total);U64 done=0;
  std::vector<std::unordered_set<unsigned>> edges(n);Plan plan;
  while(done<total) {
    std::vector<uint32_t> values;
    for(int i=0;i<n;++i)for(int j=0;j<n;++j) {
      if(residual[i*n+j]) {edges[i].insert(n+j);values.push_back(residual[i*n+j]);}
      else edges[i].erase(n+j);
    }
    std::sort(values.begin(),values.end());values.erase(std::unique(values.begin(),values.end()),values.end());
    int lo=0,hi=int(values.size())-1;std::vector<int> best;
    while(lo<=hi) {
      int mid=(lo+hi)/2;std::vector<std::vector<int>> support(n);
      for(int i=0;i<n;++i)for(unsigned col:edges[i])if(residual[i*n+col-n]>=values[mid])support[i].push_back(col-n);
      std::vector<int> trial;
      if(matching(support,trial,calls)) {best=std::move(trial);lo=mid+1;}
      else hi=mid-1;
    }
    if(best.empty())throw std::runtime_error("Max-Min positive support has no perfect matching");
    uint32_t weight=std::numeric_limits<uint32_t>::max();
    for(int i=0;i<n;++i)weight=std::min(weight,residual[i*n+best[i]]);
    if(!weight)throw std::runtime_error("zero extraction");
    for(int i=0;i<n;++i)residual[i*n+best[i]]-=weight;
    plan.push_back({weight,best});done+=weight;
    if(plan.size()>size_t(n*n))throw std::runtime_error("edge-zeroing bound exceeded");
  }
  return plan;
}

Plan native(const std::vector<uint32_t>& input,int n,int g,U64& calls) {
  Matrix m(const_cast<uint32_t*>(input.data()),n);
  FastAll2All app(&m,g,ETHER400,H100,1e-6);
  app.to_scaled_doubly_stochastic_matrix();app.decompose();Plan result;
  for(auto& p:app.p_sets) {
    Stage stage{p.get_freq(),std::vector<int>(n)};
    for(int i=0;i<n;++i)stage.map[i]=p.mp.at(i);
    result.push_back(std::move(stage));
  }
  calls+=result.size();return result;
}

double local_cost(const U64* matrix,int g,double alpha,double beta) {
  double cost=0;
  for(int offset=0;offset<g;++offset) {
    U64 largest=0;for(int i=0;i<g;++i)largest=std::max(largest,matrix[i*g+(i+offset)%g]);
    if(largest)cost+=alpha+double(largest)*beta;
  }
  return cost;
}

double evaluate(const Plan& plan,const U64* input,int n,int g,const int32_t* dc,const double* p) {
  int ranks=n*g;std::vector<U64> data(input,input+ranks*ranks),intrinsic(n*g*g,0);
  double intrinsic_cost=0;
  for(int s=0;s<n;++s) {
    for(int i=0;i<g;++i)for(int j=0;j<g;++j)intrinsic[(s*g+i)*g+j]=data[(s*g+i)*ranks+s*g+j];
    intrinsic_cost=std::max(intrinsic_cost,local_cost(intrinsic.data()+s*g*g,g,p[5],p[6]));
  }
  double total=0,previous=intrinsic_cost;
  int ndc=0;for(int s=0;s<n;++s)ndc=std::max(ndc,dc[s]+1);
  for(const auto& stage:plan) {
    std::vector<U64> dispatch(n*g*g,0),wan(ndc*ndc,0),send(ndc,0),recv(ndc,0);
    double alpha=0,serial=0;
    for(int s=0;s<n;++s) {
      int d=stage.map[s];U64 volume=0;
      for(int i=0;i<g;++i) {
        U64 left=stage.weight;
        for(int j=0;j<g;++j) {
          U64& cell=data[(s*g+i)*ranks+d*g+j];U64 take=std::min(left,cell);
          cell-=take;left-=take;
          if(i!=j)dispatch[(d*g+i)*g+j]+=take;
          if(s!=d)volume+=take;
          if(!left)break;
        }
      }
      if(s==d || !volume)continue;
      serial=std::max(serial,8.*double(volume)/p[0]);
      bool cross=dc[s]!=dc[d];alpha=std::max(alpha,p[cross?4:3]);
      if(cross) {wan[dc[s]*ndc+dc[d]]+=volume;send[dc[s]]+=volume;recv[dc[d]]+=volume;}
    }
    for(U64 v:wan)serial=std::max(serial,8.*double(v)/p[1]);
    for(int d=0;d<ndc;++d)serial=std::max(serial,8.*double(std::max(send[d],recv[d]))/p[2]);
    total+=std::max(std::ceil(alpha+serial),previous);previous=0;
    for(int d=0;d<n;++d)previous=std::max(previous,local_cost(dispatch.data()+d*g*g,g,p[5],p[6]));
  }
  if(!plan.empty())total+=previous;else total=intrinsic_cost;
  for(int s=0;s<n;++s)for(int d=0;d<n;++d)if(s!=d)
    for(int i=0;i<g;++i)for(int j=0;j<g;++j)if(data[(s*g+i)*ranks+d*g+j])
      throw std::runtime_error("compiled plan left real GPU data");
  return total;
}
}

extern "C" int fast_epoch(uint32_t n,uint32_t g,uint32_t mode,const uint32_t* matrix,
    const uint64_t* contents,const int32_t* dc,const double* params,uint32_t* weights,
    int32_t* maps,uint32_t capacity,uint64_t* stats,double* costs,char* error,uint32_t error_cap) {
  try {
    auto started=std::chrono::steady_clock::now();
    if(n<1||n>64||g<1||g>16||mode>3||!matrix||!contents||!dc||!params||!weights||!maps||!stats||!costs)
      throw std::runtime_error("unsupported kernel dimensions, mode or null pointer");
    for(int k=0;k<7;++k)if(!std::isfinite(params[k]) || (k<3?params[k]<=0:params[k]<0))
      throw std::runtime_error("invalid model parameter");
    for(uint32_t s=0;s<n;++s) {
      if(dc[s]<0 || dc[s]>=int32_t(n))throw std::runtime_error("invalid normalized DC label");
      U64 row=0,col=0;
      for(uint32_t d=0;d<n;++d){row+=matrix[s*n+d];col+=matrix[d*n+s];}
      if(row>UINT32_MAX || col>UINT32_MAX || matrix[s*n+s])throw std::runtime_error("native service range exceeded");
    }
    std::vector<uint32_t> input(matrix,matrix+n*n);Plan plans[3];U64 calls=0;
    for(int k=0;k<3;++k)costs[k]=-1;
    int chosen=mode==3?0:int(mode);
    for(int k=0;k<3;++k) {
      if(mode!=3 && k!=int(mode))continue;
      if(k==0)plans[k]=native(input,n,g,calls);
      else if(k==1)plans[k]=maxmin(input,n,calls);
      else {
        for(int cross=0;cross<2;++cross) {
          auto part=input;
          for(uint32_t s=0;s<n;++s)for(uint32_t d=0;d<n;++d)if(int(dc[s]!=dc[d])!=cross)part[s*n+d]=0;
          if(!cross)part=complete_lan(part,n,dc);
          auto stages=maxmin(part,n,calls);plans[k].insert(plans[k].end(),stages.begin(),stages.end());
        }
      }
      costs[k]=evaluate(plans[k],contents,n,g,dc,params);
      if(mode==3 && costs[k]<costs[chosen]-1e-6)chosen=k;
    }
    const auto& selected=plans[chosen];if(selected.size()>capacity)throw std::runtime_error("output capacity exceeded");
    for(size_t k=0;k<selected.size();++k) {
      weights[k]=selected[k].weight;
      for(uint32_t s=0;s<n;++s)maps[k*n+s]=selected[k].map[s];
    }
    stats[0]=selected.size();stats[1]=calls;
    stats[2]=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-started).count();
    stats[3]=chosen;return 0;
  } catch(const std::exception& e) {
    if(error && error_cap)std::snprintf(error,error_cap,"%s",e.what());return 1;
  }
}
