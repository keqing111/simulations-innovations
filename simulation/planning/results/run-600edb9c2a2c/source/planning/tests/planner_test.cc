#include "planner.h"
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <new>
#include <numeric>
#include <random>
#include <algorithm>
#include <cmath>

static bool count_allocations=false;
static size_t allocations=0;
void* operator new(std::size_t size){if(count_allocations)++allocations;if(void* p=std::malloc(size?size:1))return p;throw std::bad_alloc();}
void operator delete(void* p)noexcept{std::free(p);}
void* operator new[](std::size_t size){return ::operator new(size);}
void operator delete[](void* p)noexcept{::operator delete(p);}
using namespace native_scan;

int main(){
    native_topology::Topology t(6,1,{0,0,1,1,2,2});ResourceCache cache(t);
    std::vector<unsigned> a(36,100),order(6);std::vector<std::vector<unsigned>> neighbors(6);
    std::iota(order.begin(),order.end(),0);
    for(unsigned s=0;s<6;++s){a[s*6+s]=0;for(unsigned d=0;d<6;++d)if(s!=d)neighbors[s].push_back(d);}
    std::vector<uint64_t> rows(a.begin(),a.end());std::vector<int> ref,fast;
    assert(cache.balanced_matching(a,neighbors,100,rows,order,ref,false));
    assert(cache.balanced_matching(a,neighbors,100,rows,order,fast,true));assert(ref==fast);
    allocations=0;count_allocations=true;bool ok=cache.balanced_matching(a,neighbors,100,rows,order,ref,false);count_allocations=false;assert(ok);size_t before=allocations;
    allocations=0;count_allocations=true;ok=cache.balanced_matching(a,neighbors,100,rows,order,fast,true);count_allocations=false;assert(ok);size_t after=allocations;
    std::cout<<"Matcher allocations after warmup: "<<before<<" -> "<<after<<std::endl;
    assert(after<=4&&after*2<before); // Catches per-DFS vector growth/stable_sort allocations.

    native_topology::Topology t4(4,1,{0,0,1,1});ResourceCache c4(t4);std::vector<unsigned> o4={0,1,2,3};std::vector<uint64_t> zeros(16);
    for(const std::vector<std::vector<unsigned>>& graph:{std::vector<std::vector<unsigned>>{{0,1},{1,2},{2,3},{0}},
                                                        std::vector<std::vector<unsigned>>{{0,1,3},{1},{2},{0}},
                                                        std::vector<std::vector<unsigned>>{{0},{0},{2},{3}}}){
        std::vector<unsigned> mask(16);for(unsigned s=0;s<4;++s)for(unsigned d:graph[s])mask[s*4+d]=1;
        bool x=c4.balanced_matching(mask,graph,1,zeros,o4,ref,false),y=c4.balanced_matching(mask,graph,1,zeros,o4,fast,true);assert(x==y&&ref==fast);
        if(x)assert(ref==std::vector<int>({1,2,3,0})||ref==std::vector<int>({3,1,2,0}));
    }
    std::mt19937 rng(760001);
    for(unsigned n:{3U,6U,12U,24U,48U})for(unsigned trial=0;trial<3;++trial){
        unsigned g=2;std::vector<int> dc(n);std::vector<unsigned> residual(n*n),natural(n);std::vector<uint64_t> real(n*n*g);std::vector<std::vector<unsigned>> graph(n);
        std::iota(natural.begin(),natural.end(),0);
        for(unsigned s=0;s<n;++s){dc[s]=s*3/n;for(unsigned d=0;d<n;++d){residual[s*n+d]=s==d?1000:(rng()%4?1+rng()%100:0);
            if(residual[s*n+d])graph[s].push_back(d);if(s!=d)for(unsigned u=0;u<g;++u)real[(s*n+d)*g+u]=rng()%101;}}
        native_topology::Topology top(n,g,dc);ResourceCache current(top);
        for(unsigned q:{1U,37U}){
            bool x=current.balanced_matching(residual,graph,q,real,natural,ref,false),y=current.balanced_matching(residual,graph,q,real,natural,fast,true);
            assert(x==y&&ref==fast);auto r=current.score(ref,q,real),f=current.score(fast,q,real);assert(r.us==f.us&&r.bytes==f.bytes);
        }
    }
    std::cout<<"Equivalent buffer reuse: stable ties, multi-hop displacement, failure reuse and30 random kernel comparisons passed\n";
    for(unsigned n:{3U,4U})for(unsigned trial=0;trial<40;++trial){
        std::vector<unsigned> mask(n*n);std::vector<double> utility(n*n);std::vector<int> p(n);std::iota(p.begin(),p.end(),0);
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){mask[s*n+d]=s==d?2:rng()%3;utility[s*n+d]=rng()%7;}
        double best=-1;do{bool valid=true;double value=0;for(unsigned s=0;s<n;++s){valid=valid&&mask[s*n+p[s]]>=2;value+=utility[s*n+p[s]];}if(valid)best=std::max(best,value);}while(std::next_permutation(p.begin(),p.end()));
        auto match=maximum_weight_matching(mask,n,2,utility);double value=0;std::vector<bool> seen(n);
        for(unsigned s=0;s<n;++s){assert(match[s]>=0&&match[s]<int(n)&&!seen[match[s]]&&mask[s*n+match[s]]>=2);seen[match[s]]=true;value+=utility[s*n+match[s]];}
        assert(value==best&&match==maximum_weight_matching(mask,n,2,utility));
    }
    bool rejected=false;try{maximum_weight_matching({1,0,1,0},2,1,{1,2,3,4});}catch(const std::runtime_error&){rejected=true;}assert(rejected);
    auto utility=cache.individual_utilities(100,rows);
    assert(utility[0]==0); // Dummy diagonal never earns utility.
    for(unsigned s=0;s<6;++s)for(unsigned d=0;d<6;++d)if(s!=d){
        auto cost=t.evaluate({{s,d,100}});assert(std::abs(utility[s*6+d]-100/cost.total_us)<1e-12);}
    assert(c4.individual_utilities(100,zeros)==std::vector<double>(16));
    assert(Options::parse("SPM-opt").static_path&&Options::parse("SPM-opt").optimized);
    std::cout<<"Masked maximum-weight assignment:80 brute-force objective checks; static real-byte path utility passed\n";
}
