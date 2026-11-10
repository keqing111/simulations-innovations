// scan 的“入口契约”测试：
//   1. 把分解推迟到 prepare 钩子中执行，结果必须与原生 pipeline2 完全一致；
//   2. 声明 defer_decomposition 却没有安装 prepare 回调时，必须报错。
// 这个测试不加载任何具体扫描算法，只验证核心调度器与扩展钩子的边界。
#include "global.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <memory>
#include <stdexcept>

// time 是模型总耗时，planning 是钩子记录的规划耗时，hooks 保留完整审计信息。
struct Result {double time, planning; pipeline_hooks_t hooks;};
// deferred=false：走原生路径（pipeline2 内部自行分解）；
// deferred=true ：在 prepare 中手动完成 SDSM + decompose，模拟 scan 的接管方式。
static Result run(bool deferred) {
    const unsigned n=3,g=2,r=n*g;
    // 构造一个非负、无自环的伪随机矩阵；系数只是为了让所有 cell 不同。
    unsigned data[r*r];
    for(unsigned s=0;s<r;++s)for(unsigned d=0;d<r;++d)data[s*r+d]=s==d?0:100+(s*71+d*31)%1000;
    std::vector<std::unique_ptr<LocalScheduler>> owned;
    std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<n;++s){owned.emplace_back(new LocalScheduler(data+s*g*r,g,n,s,H100));locals.push_back(owned.back().get());}
    GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
    pipeline_hooks_t hooks;
    hooks.defer_decomposition=deferred;
    // prepare 在 server 内 balance 之后、通信之前运行，拿得到尚未分解的 FastAll2All。
    hooks.prepare=[&](Matrix&,const std::vector<uint64_t>&,FastAll2All& app){
        if(deferred){assert(app.p_sets.empty());app.to_scaled_doubly_stochastic_matrix();app.decompose();}
        else assert(!app.p_sets.empty());
    };
    auto value=scheduler.pipeline2(1e-6,&balance_alpha_beta,&hooks);
    if(deferred)assert(hooks.native_decompose_us==0); // 原生分解没有运行
    assert(hooks.planning_us>0);                      // 钩子路径被计入规划时间
    return {value.t,hooks.planning_us,hooks};
}
int main(){
    // 同一输入、同一随机种子下，原生分解和钩子内分解必须得到相同时间与相同阶段序列。
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
    // 负例：只打开 defer_decomposition，不提供 prepare，应明确拒绝而不是静默产出空计划。
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
