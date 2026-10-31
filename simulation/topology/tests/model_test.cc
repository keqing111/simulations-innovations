#include "model.h"
#include <cassert>
#include <cmath>
#include <iostream>

bool near(double a,double b){return std::abs(a-b)<1e-7;}
int main(){
  native_topology::Topology t(4,1,{0,0,1,1},400,400,100,5000);
  auto c=t.evaluate({{0,2,100000000},{1,3,100000000}});
  assert(near(c.alpha_us,5002.8));assert(near(c.serial_us,16000));
  assert(near(c.total_us,21002.8));assert(c.bytes.at("wan:0:1")==200000000);
  auto duplex=t.evaluate({{0,2,100000000},{2,0,100000000}});
  assert(near(duplex.serial_us,8000));
  assert(near(t.evaluate({{0,1,100000000}}).total_us,2001.4));
  assert(t.evaluate({{0,2,0},{0,0,12345}}).total_us==0);
  auto h=native_topology::Topology::homogeneous(2,4,22,.7);
  assert(near(h.evaluate({{0,1,4000000}}).total_us,22.7));
  // Same bytes and two matchings, different shared-link placement. Benefit
  // reverses at one-way WAN1998.6us: local parallelism vs repeated alpha.
  std::vector<unsigned> lan={1,0,3,2,5,4},wan={3,2,5,4,1,0},m1={3,0,5,2,1,4},m2={1,2,3,4,5,0};
  auto plan_cost=[&](const native_topology::Topology& net,const std::vector<unsigned>& map){std::vector<native_topology::Transfer> f;for(unsigned s=0;s<6;++s)f.push_back({s,map[s],100000000});return net.evaluate(f).total_us;};
  native_topology::Topology short_wan(6,1,{0,0,1,1,2,2},400,400,100,50);
  assert(near(plan_cost(short_wan,lan)+plan_cost(short_wan,wan),18054.2));
  assert(near(plan_cost(short_wan,m1)+plan_cost(short_wan,m2),16105.6));
  native_topology::Topology long_wan(6,1,{0,0,1,1,2,2},400,400,100,5000);
  assert(near(plan_cost(long_wan,lan)+plan_cost(long_wan,wan),23004.2));
  assert(near(plan_cost(long_wan,m1)+plan_cost(long_wan,m2),26005.6));
  bool bad=false;try{native_topology::Topology fail(4,1,{0},400,400,100,5000);}catch(...){bad=true;}assert(bad);
  bad=false;try{t.evaluate({{5,0,10}});}catch(...){bad=true;}assert(bad);
  bad=false;try{native_topology::Topology fail(4,1,{0,0,1,1},0,400,100,5000);}catch(...){bad=true;}assert(bad);
  std::cout<<"native topology hand calculations passed\n";
}
