#include "matching.h"
#include <cassert>
#include <iostream>
#include <set>
int main(){
  using namespace native_topology;
  // Two alternating 2-cycles: force one WAN edge without losing perfection.
  std::vector<unsigned> a={1,1,0,0, 1,1,0,0, 0,0,1,1, 0,0,1,1};
  std::vector<int> m={0,1,2,3};
  assert(force_edge(a,4,m,0,1));assert(m[0]==1 && m[1]==0);
  assert(std::set<int>(m.begin(),m.end()).size()==4);
  assert(!force_edge(a,4,m,0,2));
  unsigned n=6,g=1;std::vector<unsigned> input(n*n,0);
  for(unsigned s=0;s<n;++s){input[s*n+(s^1)]=100;input[s*n+(s+2)%n]=200;}
  Matrix matrix(input.data(),n);std::vector<uint64_t> rows(input.begin(),input.end());
  Topology top(n,g,{0,0,1,1,2,2});
  for(auto mode:{"wan-repair","link-cost","link-cost-wan","maxmin"}){
    Planner planner(top,rows,mode);auto stages=planner.decompose(matrix);
    std::vector<uint64_t> total(n*n,0);
    for(auto& p:stages){std::set<unsigned> seen;for(unsigned s=0;s<n;++s){seen.insert(p.mp.at(s));total[s*n+p.mp.at(s)]+=p.get_freq();}assert(seen.size()==n);}
    assert(stages.size()<=n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)assert(total[s*n+d]>=input[s*n+d]);
    assert(planner.remaining_inter_bytes()==0);
    assert(planner.cycle_trials<=n*stages.size()+stages.size());
  }
  std::cout<<"native matching conservation and finite-bound checks passed\n";
}
