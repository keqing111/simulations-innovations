// topology 资源模型的纯手算测试。
// 4 server、每 DC 2 个、WAN 100Gbps/5000us；所有期望值都可以在纸上复算。
// 覆盖：跨 DC 串行聚合、全双工、同 DC 路径、零流量、uniform 模型、
// 两种匹配在短/长 WAN 下的收益反转，以及参数/端点的错误拒绝。
#include "model.h"
#include <cassert>
#include <cmath>
#include <iostream>

bool near(double a,double b){return std::abs(a-b)<1e-7;}
int main(){
  native_topology::Topology t(4,1,{0,0,1,1},400,400,100,5000);
  // 两条 1e8 B 流同向共享 wan:0:1：serial=2*8e8bit/100Gbps=16000us，
  // alpha=5000+2.8；不同 server 的 server/gateway 资源不构成瓶颈。
  auto c=t.evaluate({{0,2,100000000},{1,3,100000000}});
  assert(near(c.alpha_us,5002.8));assert(near(c.serial_us,16000));
  assert(near(c.total_us,21002.8));assert(c.bytes.at("wan:0:1")==200000000);
  // 全双工：两个方向是不同 WAN 资源，各 8000us 并行，瓶颈仍 8000us。
  auto duplex=t.evaluate({{0,2,100000000},{2,0,100000000}});
  assert(near(duplex.serial_us,8000));
  // 同 DC：400Gbps server 链路，800Mbit/400Gbps=2000us + 1.4us。
  assert(near(t.evaluate({{0,1,100000000}}).total_us,2001.4));
  // 零字节与本地流被忽略，结果保持 0。
  assert(t.evaluate({{0,2,0},{0,0,12345}}).total_us==0);
  // homogeneous：4 GPU × 1MB 恰好 22us，加固定 .7us。
  auto h=native_topology::Topology::homogeneous(2,4,22,.7);
  assert(near(h.evaluate({{0,1,4000000}}).total_us,22.7));
  // Same bytes and two matchings, different shared-link placement. Benefit
  // reverses at one-way WAN1998.6us: local parallelism vs repeated alpha.
  // lan+wan 是两个“本地/跨 DC 分开”的阶段；m1+m2 是两个混合阶段。
  // WAN 50us 时混合更优（16105.6 < 18054.2），WAN 5000us 时反而更差
  // （26005.6 > 23004.2）：跨 DC 延迟重复付出的代价超过并行收益。
  std::vector<unsigned> lan={1,0,3,2,5,4},wan={3,2,5,4,1,0},m1={3,0,5,2,1,4},m2={1,2,3,4,5,0};
  auto plan_cost=[&](const native_topology::Topology& net,const std::vector<unsigned>& map){std::vector<native_topology::Transfer> f;for(unsigned s=0;s<6;++s)f.push_back({s,map[s],100000000});return net.evaluate(f).total_us;};
  native_topology::Topology short_wan(6,1,{0,0,1,1,2,2},400,400,100,50);
  assert(near(plan_cost(short_wan,lan)+plan_cost(short_wan,wan),18054.2));
  assert(near(plan_cost(short_wan,m1)+plan_cost(short_wan,m2),16105.6));
  native_topology::Topology long_wan(6,1,{0,0,1,1,2,2},400,400,100,5000);
  assert(near(plan_cost(long_wan,lan)+plan_cost(long_wan,wan),23004.2));
  assert(near(plan_cost(long_wan,m1)+plan_cost(long_wan,m2),26005.6));
  // 错误输入必须拒绝：DC 标签数与 n 不符、越界端点、零带宽。
  bool bad=false;try{native_topology::Topology fail(4,1,{0},400,400,100,5000);}catch(...){bad=true;}assert(bad);
  bad=false;try{t.evaluate({{5,0,10}});}catch(...){bad=true;}assert(bad);
  bad=false;try{native_topology::Topology fail(4,1,{0,0,1,1},0,400,100,5000);}catch(...){bad=true;}assert(bad);
  std::cout<<"native topology hand calculations passed\n";
}
