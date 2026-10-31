#include "planner.h"
#include "cached.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <random>
#include <set>
using namespace native_scan;
using native_topology::Topology;

static hungarian_info_t info_for(const std::vector<unsigned>& a,unsigned n,const std::vector<int>& m){
    hungarian_info_t info;info.matching.assign(2*n,-1);info.row_to_col.resize(n);
    for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;
        for(unsigned d=0;d<n;++d)if(a[s*n+d])info.row_to_col[s].insert(n+d);}
    return info;
}
static void equal(const std::vector<PermutationSet>& a,const std::vector<PermutationSet>& b){
    assert(a.size()==b.size());for(unsigned k=0;k<a.size();++k){assert(a[k].mp==b[k].mp);assert(a[k].get_freq()==b[k].get_freq());}
}
int main(){
    bool invalid=false;try{Options::parse("F200");}catch(const std::invalid_argument&){invalid=true;}assert(invalid);
    // A low coefficient alternating cycle must not pass L's guard.
    std::vector<unsigned> a={0,10,1,1,0,10,10,1,0};
    std::vector<uint64_t> rows(a.begin(),a.end());
    Topology top3(3,1,{0,1,2},400,400,100,50);
    Planner guarded(top3,rows,"F010");auto info=info_for(a,3,{1,2,0});
    auto choice=guarded.choose(a,info);
    assert(choice.matching==std::vector<int>({1,2,0}));assert(choice.weight==10);
    assert(guarded.stats().lower_coefficient_rejections>0);

    // Shared-WAN cost and current real-only residual priority, hand computed.
    Topology top6(6,1,{0,0,1,1,2,2},400,400,100,50);
    ResourceCache resources(top6);std::vector<uint64_t> real(36);
    real[0*6+2]=100000000;real[1*6+3]=100000000;
    auto cost=resources.score({2,3,0,1,5,4},100000000,real);
    assert(cost.bytes==200000000);assert(std::abs(cost.us-16052.8)<1e-8);
    auto priority=resources.priorities(real);
    assert(priority[2]>0 && priority[9]>0 && priority[4]==0 && priority[0]==0);
    auto empty=resources.priorities(std::vector<uint64_t>(36));for(double v:empty)assert(v==0);

    // G has a feasible coarse bound even at the uint32 upper end.
    std::vector<unsigned> big={UINT32_MAX,0,0,UINT32_MAX};
    Topology top2(2,1,{0,1});Planner large(top2,std::vector<uint64_t>(4),"F100");
    auto hi=large.choose(big,info_for(big,2,{0,1}));assert(hi.weight==UINT32_MAX);
    assert(hi.threshold>=UINT32_MAX/2);assert(large.stats().threshold_probes<=6);
    std::vector<unsigned> uneven={1,100,100,1};auto small_info=info_for(uneven,2,{0,1});
    Planner coarse(top2,{0,100,100,0},"F100");auto coarse_choice=coarse.choose(uneven,small_info);
    assert(coarse_choice.threshold==64 && coarse_choice.weight==100);
    std::vector<unsigned> flat={100,100,100,100};auto flat_info=info_for(flat,2,{0,1});
    Planner ordered(top2,{0,100,100,0},"F001");
    assert(ordered.choose(flat,flat_info).matching==std::vector<int>({1,0}));

    std::mt19937 rng(291029);
    for(unsigned n:{3U,6U,12U})for(unsigned trial=0;trial<8;++trial){
        unsigned g=2;std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
        Topology top(n,g,dc,400,400,100,50);std::vector<unsigned> values(n*n);
        std::vector<uint64_t> actual(n*n*g);
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){values[s*n+d]=s==d?0:3+rng()%3000;
            for(unsigned u=0;u<g;++u)actual[(s*n+d)*g+u]=s==d?0:values[s*n+d]-u;}
        Matrix service(values.data(),n);FastAll2All native(&service,g,ETHER400,H100,1e-6);
        native.to_scaled_doubly_stochastic_matrix();native.decompose();
        native_topology::set_variant("bounded");
        Matrix oldservice(values.data(),n);native_topology::CachedPlanner bounded(top,actual,"link-cost");auto old=bounded.decompose(oldservice);
        Matrix exactservice(values.data(),n);native_topology::Planner exact(top,actual,"maxmin");auto want_exact=exact.decompose(exactservice);
        for(const std::string& method:{"F000","F001","F010","F011","F100","F101","F110","F111","maxmin"}){
            Matrix matrix(values.data(),n);FastAll2All app(&matrix,g,ETHER400,H100,1e-6);
            Planner planner(top,actual,method);planner.decompose_into(app);
            if(method=="F000")equal(native.p_sets,app.p_sets);
            if(method=="F010")equal(old,app.p_sets);
            if(method=="maxmin")equal(want_exact,app.p_sets);
            assert(app.verify_decomposition());assert(planner.remaining_inter_bytes()==0);
            assert(app.p_sets.size()<=n*n);assert(planner.stats().decomposition_runs==1);
            assert(planner.stats().cycle_trials<=2*app.p_sets.size());
            for(auto c:planner.stats().decisions){assert(c.weight>0 && c.weight>=c.threshold);
                if(method[0]=='F' && method[1]=='1')assert(c.probes<=6);
                assert(c.weight>=c.seed_weight);}
        }
    }
    Matrix zeros(3);FastAll2All empty_app(&zeros,1,ETHER400,H100,1e-6);
    Planner empty_planner(top3,std::vector<uint64_t>(9),"F111");empty_planner.decompose_into(empty_app);
    assert(empty_app.p_sets.empty());assert(empty_planner.remaining_inter_bytes()==0);
    // One real local-DC edge plus padding: only original real bytes survive.
    std::vector<unsigned> one(36);one[1]=17;Matrix matrix(one.data(),6);
    FastAll2All app(&matrix,1,ETHER400,H100,1e-6);std::vector<uint64_t> one_real(one.begin(),one.end());
    Planner only_local(top6,one_real,"F111");only_local.decompose_into(app);
    assert(only_local.remaining_inter_bytes()==0);assert(app.verify_decomposition());
    std::cout<<"G/L/R native/bounded/maxmin identity, support/real conservation, coefficient guard, resource pressure and uint32 tests passed\n";
}
