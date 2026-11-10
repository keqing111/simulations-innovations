// topology 匹配修补测试：
//   1. force_edge 能沿交替环把指定边塞进完美匹配，且保持双射；
//   2. 不支持/不可达的边必须返回 false；
//   3. 四个 Planner 模式都保持阶段数有界、每阶段是完美匹配、
//      累计频次覆盖原始需求、真实剩余清零。
#include "matching.h"
#include <cassert>
#include <iostream>
#include <set>
int main(){
  using namespace native_topology;
  // Two alternating 2-cycles: force one WAN edge without losing perfection.
  // 支持图分成 {0,1} 和 {2,3} 两个完全二部块；当前匹配是恒等。
  // 把 0->1 塞进去会触发 0<->1 的 2-环交换，2/3 不受影响。
  std::vector<unsigned> a={1,1,0,0, 1,1,0,0, 0,0,1,1, 0,0,1,1};
  std::vector<int> m={0,1,2,3};
  assert(force_edge(a,4,m,0,1));assert(m[0]==1 && m[1]==0);
  assert(std::set<int>(m.begin(),m.end()).size()==4); // 仍是完美匹配
  assert(!force_edge(a,4,m,0,2));                     // 0 与 2 不在同一交替分量
  // 6 server、每对相邻/隔一个的 server 有 100/200 的真实需求。
  // 只用 g=1，方便把 rows 直接当作 server 级矩阵。
  unsigned n=6,g=1;std::vector<unsigned> input(n*n,0);
  for(unsigned s=0;s<n;++s){input[s*n+(s^1)]=100;input[s*n+(s+2)%n]=200;}
  Matrix matrix(input.data(),n);std::vector<uint64_t> rows(input.begin(),input.end());
  Topology top(n,g,{0,0,1,1,2,2});
  for(auto mode:{"wan-repair","link-cost","link-cost-wan","maxmin"}){
    Planner planner(top,rows,mode);auto stages=planner.decompose(matrix);
    // 每个阶段必须是完美匹配；把频次累加回去应覆盖（可能因补齐而超过）原需求。
    std::vector<uint64_t> total(n*n,0);
    for(auto& p:stages){std::set<unsigned> seen;for(unsigned s=0;s<n;++s){seen.insert(p.mp.at(s));total[s*n+p.mp.at(s)]+=p.get_freq();}assert(seen.size()==n);}
    assert(stages.size()<=n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)assert(total[s*n+d]>=input[s*n+d]);
    assert(planner.remaining_inter_bytes()==0);
    // link-cost 每阶段最多试 n 条边，WAN 修复每阶段最多 1 次，故上界 n+1。
    assert(planner.cycle_trials<=n*stages.size()+stages.size());
  }
  std::cout<<"native matching conservation and finite-bound checks passed\n";
}
