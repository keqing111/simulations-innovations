#include "planner.h"
#include <cassert>
#include <iostream>
#include <numeric>
#include <random>
using namespace native_scan;
int main(){
    std::vector<unsigned> a={0,8,2,2,0,8,8,2,0};std::vector<int> m={1,2,0};std::vector<uint64_t> real(a.begin(),a.end());
    auto unique=support_summary(a,3,8,m,real,1);assert(unique.edges==3&&unique.movable_rows==0&&unique.flexible_edges==0);
    auto cycle=support_summary(a,3,2,m,real,1);assert(cycle.edges==6&&cycle.movable_rows==3&&cycle.flexible_edges==3&&cycle.real_movable_rows==3);
    std::fill(real.begin(),real.end(),0);assert(support_summary(a,3,2,m,real,1).real_movable_rows==0);
    real[1]=8;assert(support_summary(a,3,2,m,real,1).real_movable_rows==1);
    auto triangular=support_summary({5,5,0,0,5,0,0,0,5},3,5,{0,1,2},std::vector<uint64_t>(9),1);
    assert(triangular.edges==4&&triangular.movable_rows==0); // Extra edge alone is not an alternating cycle.
    std::vector<unsigned> scarce={1,1,1,1,1,0,0,0,0,1,1,0,0,0,0,1};std::vector<double> pressure={10,1,9,5};
    assert(constructive_order(scarce,4,1,{},0)==std::vector<unsigned>({0,1,2,3}));
    assert(constructive_order(scarce,4,1,{},1)==std::vector<unsigned>({1,3,2,0}));
    assert(constructive_order(scarce,4,1,pressure,2)==std::vector<unsigned>({0,2,3,1}));
    assert(constructive_order(scarce,4,1,pressure,3)==std::vector<unsigned>({3,1,2,0}));
    std::vector<unsigned> full(16,1);for(unsigned i=0;i<4;++i)full[i*4+i]=0;
    std::vector<uint64_t> demand(16);demand[2]=100;demand[8]=100;
    native_topology::Topology t(4,1,{0,0,1,1});t.bandwidths["wan:0:1"]=25;
    auto first=ResourceCache(t).row_priorities(full,1,demand);assert(first[0]>first[2]&&first[1]==0&&first[3]==0);
    t.bandwidths["wan:0:1"]=100;t.bandwidths["wan:1:0"]=25;
    auto second=ResourceCache(t).row_priorities(full,1,demand);assert(second[2]>second[0]);
    std::mt19937 rng(640001);
    for(unsigned n:{3U,6U,12U,24U,48U})for(unsigned k=0;k<3;++k){unsigned g=2;
        std::vector<int> dc(n);std::vector<unsigned> values(n*n);std::vector<uint64_t> rows(n*n*g);
        for(unsigned s=0;s<n;++s){dc[s]=s*3/n;for(unsigned d=0;d<n;++d)if(s!=d){values[s*n+d]=(rng()%4)?2+rng()%500:0;
            for(unsigned u=0;u<g;++u)rows[(s*n+d)*g+u]=values[s*n+d]?values[s*n+d]-u:0;}}
        native_topology::Topology top(n,g,dc);
        for(const std::string method:{"A1D","A1P","A1DP"}){
            Matrix matrix(values.data(),n);FastAll2All app(&matrix,g,ETHER400,H100,1e-6);Planner planner(top,rows,method);planner.decompose_into(app);
            assert(app.verify_decomposition()&&planner.remaining_inter_bytes()==0);assert(planner.stats().construction_trials==app.p_sets.size());
            for(const auto& d:planner.stats().decisions)assert(d.constructions==1&&d.weight==d.exact_weight&&d.weight>=d.seed_weight);
        }
    }
    std::cout<<"Support freedom, virtual distinction, literal row orders, capacity-sensitive pressure and45 sparse decompositions passed\n";
}
