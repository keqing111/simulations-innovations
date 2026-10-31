#include "cached.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <random>
using namespace native_topology;
int main(){
  Topology top(6,1,{0,0,1,1,2,2},400,400,100,50);
  CachedScorer scorer(top);std::vector<uint64_t> rows(36,0);rows[2]=rows[9]=100000000;
  std::vector<int> m={2,3,0,1,4,5};auto value=scorer.score(m,100000000,rows);
  assert(value.bytes==200000000);assert(std::abs(value.us-16052.8)<1e-8);
  Topology relabeled(6,1,{2,2,0,0,1,1},400,400,100,50);CachedScorer renamed(relabeled);
  assert(std::abs(renamed.score(m,100000000,rows).us-16052.8)<1e-8);
  Topology homogeneous=Topology::homogeneous(6,8,20,.7);CachedScorer paper_ports(homogeneous);
  std::vector<uint64_t> per_gpu(6*6*8,1000);
  assert(std::abs(paper_ports.score({1,2,3,4,5,0},1000,per_gpu).us-.72)<1e-10);
  auto empty=scorer.score(m,0,rows);assert(empty.bytes==0&&empty.us==0);
  rows[4*6+4]=999;auto no_self=scorer.score(m,100000000,rows);assert(no_self.bytes==200000000);
  std::mt19937 rng(123);
  for(unsigned n:{6U,12U})for(unsigned g:{1U,8U}){
    std::vector<int> dc(n);for(unsigned i=0;i<n;++i)dc[i]=i%3;
    Topology t(n,g,dc,400,400,100,5000);CachedScorer fast(t);
    for(unsigned trial=0;trial<100;++trial){
      std::vector<uint64_t> remaining(n*n*g);for(auto& v:remaining)v=rng()%1000;
      std::vector<int> matching(n);for(unsigned i=0;i<n;++i)matching[i]=(i+trial)%n;
      unsigned w=rng()%1200;std::vector<Transfer> f;uint64_t total=0;
      for(unsigned s=0;s<n;++s){uint64_t bytes=0;for(unsigned i=0;i<g;++i)bytes+=std::min<uint64_t>(w,remaining[(s*n+matching[s])*g+i]);
        f.push_back({s,unsigned(matching[s]),bytes});if(s!=unsigned(matching[s]))total+=bytes;}
      auto a=fast.score(matching,w,remaining);auto b=t.evaluate(f);
      assert(a.bytes==total);assert(std::abs(a.us-b.total_us)<1e-8);
    }
  }
  bool rejected=false;try{scorer.score({0,0,2,3,4,5},1,rows);}catch(const std::invalid_argument&){rejected=true;}assert(rejected);
  std::cout<<"Numeric route scorer: hand bounds,400map comparisons,virtual/self and validation passed\n";
}
