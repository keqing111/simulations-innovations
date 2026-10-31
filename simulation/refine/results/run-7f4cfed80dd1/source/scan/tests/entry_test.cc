#include "global.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <memory>
#include <stdexcept>

struct Result {double time, planning; pipeline_hooks_t hooks;};
static Result run(bool deferred) {
    const unsigned n=3,g=2,r=n*g;
    unsigned data[r*r];
    for(unsigned s=0;s<r;++s)for(unsigned d=0;d<r;++d)data[s*r+d]=s==d?0:100+(s*71+d*31)%1000;
    std::vector<std::unique_ptr<LocalScheduler>> owned;
    std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<n;++s){owned.emplace_back(new LocalScheduler(data+s*g*r,g,n,s,H100));locals.push_back(owned.back().get());}
    GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
    pipeline_hooks_t hooks;
    hooks.defer_decomposition=deferred;
    hooks.prepare=[&](Matrix&,const std::vector<uint64_t>&,FastAll2All& app){
        if(deferred){assert(app.p_sets.empty());app.to_scaled_doubly_stochastic_matrix();app.decompose();}
        else assert(!app.p_sets.empty());
    };
    auto value=scheduler.pipeline2(1e-6,&balance_alpha_beta,&hooks);
    if(deferred)assert(hooks.native_decompose_us==0);
    assert(hooks.planning_us>0);
    return {value.t,hooks.planning_us,hooks};
}
int main(){
    auto old=run(false),single=run(true);
    assert(old.time==single.time);
    assert(old.hooks.balance_us==single.hooks.balance_us);
    assert(old.hooks.intrinsic_us==single.hooks.intrinsic_us);
    assert(old.hooks.dispatch_us==single.hooks.dispatch_us);
    assert(old.hooks.payload_units==single.hooks.payload_units);
    assert(old.hooks.stages.size()==single.hooks.stages.size());
    for(unsigned k=0;k<old.hooks.stages.size();++k){
        assert(old.hooks.stages[k].mp==single.hooks.stages[k].mp);
        assert(old.hooks.stages[k].get_freq()==single.hooks.stages[k].get_freq());
    }
    unsigned zero[4]={0,0,0,0};
    LocalScheduler local(zero,1,2,0,H100),other(zero+2,1,2,1,H100);
    std::vector<LocalScheduler*> locals={&local,&other};
    GlobalScheduler scheduler(2,1,locals,ETHER400,H100,false,1e-6);
    pipeline_hooks_t hooks;hooks.defer_decomposition=true;
    bool rejected=false;
    try{scheduler.pipeline2(1e-6,&balance_alpha_beta,&hooks);}catch(const std::invalid_argument&){rejected=true;}
    assert(rejected);
    std::cout<<"single decomposition/native identity and missing callback rejection passed\n";
}
