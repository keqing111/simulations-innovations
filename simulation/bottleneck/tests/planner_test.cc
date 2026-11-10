// bottleneck Planner 性质测试：
//   - cached 必须与 topology::Planner(reference) 逐阶段完全一致（含审计计数）；
//   - bounded 只做有界搜索，不要求与 reference 相同，但必须满足：
//       每阶段是完美匹配、频次 >0、逐 GPU 真实剩余最终清零、阶段数有界、
//       每阶段最多尝试 2 条候选边。
// 共 n∈{6,12} × 12 个随机矩阵。
#include "cached.h"
#include <cassert>
#include <iostream>
#include <random>
#include <set>
using namespace native_topology;
int main(){
  std::mt19937 rng(1122);
  for(unsigned n:{6U,12U})for(unsigned trial=0;trial<12;++trial){
    // 每 server 的 g 个 GPU 都接近同一个 server 级值（逐 GPU 减 i%3），
    // 模拟 balance 后略有差异的真实剩余。
    unsigned g=4;std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
    Topology top(n,g,dc,400,400,100,50);std::vector<unsigned> a(n*n);std::vector<uint64_t> rows(n*n*g);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){a[s*n+d]=s==d?0:3+rng()%10000;
      for(unsigned i=0;i<g;++i)rows[(s*n+d)*g+i]=a[s*n+d]?a[s*n+d]-i%3:0;}
    // 基准：reference 变体本质上就是 topology::Planner 的 link-cost。
    Matrix service(a.data(),n);Planner reference(top,rows,"link-cost");auto expected=reference.decompose(service);
    set_variant("cached");Matrix copy(a.data(),n);CachedPlanner cached(top,rows,"link-cost");auto actual=cached.decompose(copy);
    assert(actual.size()==expected.size());for(unsigned k=0;k<actual.size();++k){assert(actual[k].mp==expected[k].mp);assert(actual[k].get_freq()==expected[k].get_freq());}
    assert(cached.cycle_trials==reference.cycle_trials);assert(cached.changed_matches==reference.changed_matches);
    // bounded：用独立副本运行，检查通用守恒性质。
    set_variant("bounded");Matrix copy2(a.data(),n);CachedPlanner bounded(top,rows,"link-cost");auto stages=bounded.decompose(copy2);
    auto remaining=rows;
    for(const auto& p:stages){std::set<unsigned> destinations;
      for(unsigned s=0;s<n;++s){unsigned d=p.mp.at(s);destinations.insert(d);for(unsigned i=0;i<g;++i){auto& v=remaining[(s*n+d)*g+i];v-=std::min<uint64_t>(p.get_freq(),v);}}
      assert(destinations.size()==n);assert(p.get_freq()>0);}
    for(auto v:remaining)assert(v==0);
    assert(bounded.remaining_inter_bytes()==0);assert(stages.size()<=n*n);assert(bounded.cycle_trials<=2*stages.size());
  }
  std::cout<<"Cached/ref matching identity and bounded-cycle conservation24matrices passed\n";
}
