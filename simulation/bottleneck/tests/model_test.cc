// CachedScorer 数值等价性测试：
//   - 手算的共享 WAN 场景；
//   - DC 标签重排不改变结果；
//   - uniform 模型；
//   - 空系数、本地流忽略；
//   - 随机 400 组与 Topology::evaluate 对比（n=6/12, g=1/8）；
//   - 非双射匹配必须拒绝。
#include "cached.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <random>
using namespace native_topology;
int main(){
  // 两条真实流 0->2、1->3 都跨 DC 且共享 wan:0:1，手算 16052.8us。
  Topology top(6,1,{0,0,1,1,2,2},400,400,100,50);
  CachedScorer scorer(top);std::vector<uint64_t> rows(36,0);rows[2]=rows[9]=100000000;
  std::vector<int> m={2,3,0,1,4,5};auto value=scorer.score(m,100000000,rows);
  assert(value.bytes==200000000);assert(std::abs(value.us-16052.8)<1e-8);
  // 仅重新编号 DC（2,2,0,0,1,1），路径拓扑同构，结果必须相同。
  Topology relabeled(6,1,{2,2,0,0,1,1},400,400,100,50);CachedScorer renamed(relabeled);
  assert(std::abs(renamed.score(m,100000000,rows).us-16052.8)<1e-8);
  // uniform：g=8、beta=20us/MB、alpha=.7。每个 server 对发 8*1000B=8000B，
  // 资源带宽 8*8000/20=3200Gbps，串行 0.008*8000/3200=0.02us，
  // 总时间 = .7 + .02 = .72us。
  Topology homogeneous=Topology::homogeneous(6,8,20,.7);CachedScorer paper_ports(homogeneous);
  std::vector<uint64_t> per_gpu(6*6*8,1000);
  assert(std::abs(paper_ports.score({1,2,3,4,5,0},1000,per_gpu).us-.72)<1e-10);
  // w=0 时不搬任何真实字节，代价为 0。
  auto empty=scorer.score(m,0,rows);assert(empty.bytes==0&&empty.us==0);
  // 本地流 (4,4) 必须被忽略，不能改变结果。
  rows[4*6+4]=999;auto no_self=scorer.score(m,100000000,rows);assert(no_self.bytes==200000000);
  // 随机对比：构造与 scorer 相同语义的 Transfer 列表交给 Topology::evaluate，
  // 字节数和总 us 必须在浮点容差内一致。共 2*2*100=400 组。
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
  // 重复目的地的“匹配”不是置换，必须被拒绝而不是静默打分。
  bool rejected=false;try{scorer.score({0,0,2,3,4,5},1,rows);}catch(const std::invalid_argument&){rejected=true;}assert(rejected);
  std::cout<<"Numeric route scorer: hand bounds,400map comparisons,virtual/self and validation passed\n";
}
