#include "topology/model.h"
#include "algorithm/all2all.h"
#include "scheduler/global.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <memory>
#include <cstdlib>
#include <new>
#include <random>
#include <numeric>
#include <set>
#include <algorithm>

// Count raw array lifetimes only; snapshots exclude pre-existing Local ownership.
static size_t live_arrays=0;
void* operator new[](size_t bytes) {
    void* p=std::malloc(bytes?bytes:1);
    if(!p)throw std::bad_alloc();
    ++live_arrays;return p;
}
void operator delete[](void* p) noexcept {
    if(p){--live_arrays;std::free(p);}
}

using namespace native_topology;
static void near(double got, double want) {
    assert(std::abs(got-want) <= 1e-9*std::max(1.,std::abs(want)));
}
template<class F> static void rejects(F fn) {
    bool rejected=false;
    try { fn(); } catch(const std::exception&) { rejected=true; }
    assert(rejected);
}
static void topology_tests() {
    std::vector<ResourceInfo> resources={{"shared",100},{"private",400}};
    std::vector<PathInfo> paths(16);
    paths[0*4+2]={{0},5,10};
    paths[1*4+3]={{0,1},7,14};
    auto top=Topology::from_fixed_routes(2,2,resources,paths);
    near(top.path_min_bandwidth_gbps(1,3),100);
    assert(top.gpu_path(1,3).resource_ids==std::vector<unsigned>({0,1}));
    auto cost=top.evaluate_gpu({{0,2,100000000},{1,3,50000000}});
    near(cost.alpha_us,7); near(cost.serial_us,12000); near(cost.total_us,12007);
    near(cost.resource_bytes[0],150000000); near(cost.resource_bytes[1],50000000);
    near(top.evaluate_gpu({{0,2,.5}}).serial_us,.00004);
    near(top.evaluate_gpu({}).total_us,0);
    near(top.evaluate_gpu({{0,0,10},{0,2,0}}).total_us,0);
    rejects([&]{ top.path(0,1); });
    rejects([&]{ top.path_alpha(0,1); });
    rejects([&]{ top.evaluate({{0,1,10}}); });
    rejects([&]{ top.evaluate_gpu({{4,2,1}}); });
    rejects([&]{ top.evaluate_gpu({{0,2,-1}}); });
    rejects([&]{ top.evaluate_gpu({{0,2,std::numeric_limits<double>::infinity()}}); });
    rejects([&]{ top.evaluate_gpu({{2,0,1}}); }); // missing active route
    auto bad=paths; bad[2].resource_ids={2};
    rejects([&]{ Topology::from_fixed_routes(2,2,resources,bad); });
    auto legacy=Topology(2,2,{0,1},400,400,100,5000);
    near(legacy.evaluate({{0,1,150000000}}).total_us,17002.8);
    near(legacy.evaluate_gpu({{0,2,100000000},{1,3,50000000}}).total_us,17002.8);
    auto uniform=Topology::homogeneous(2,2,20,.7);
    near(uniform.evaluate_gpu({{0,2,1000000},{1,3,1000000}}).total_us,20.7);
    resources[0].bandwidth_gbps=0;
    rejects([&]{ Topology::from_fixed_routes(2,2,resources,paths); });
}
static void a2a_tests() {
    std::vector<uint> data(36,100000000);
    for(unsigned s=0;s<6;++s)data[s*6+s]=0;
    Matrix matrix(data.data(),6);
    auto run=[&](ScheduleMode mode,unsigned gateway,unsigned wan) {
        Topology top(6,1,{0,0,1,1,2,2},400,gateway,wan,5000);
        FastAll2All app(&matrix,1,ETHER400,H100,1e-6);
        app.to_scaled_doubly_stochastic_matrix();
        std::vector<uint64_t> pending(data.begin(),data.end()),before=pending;
        app.decompose(mode,top,pending);
        assert(pending==before);assert(app.verify_decomposition());
        double total=0;
        for(const auto& p:app.p_sets) {
            std::vector<GpuTransfer> flows;
            for(unsigned s=0;s<6;++s) {
                unsigned d=p.mp.at(s);auto& v=pending[s*6+d];
                auto x=std::min<uint64_t>(p.get_freq(),v);v-=x;
                if(s!=d)flows.push_back({s,d,double(x)});
            }
            total+=top.evaluate_gpu(flows).total_us;
        }
        for(auto v:pending)assert(v==0);
        assert(app.p_sets.size()==5);return total;
    };
    near(run(ScheduleMode::Native,400,100),81014);
    near(run(ScheduleMode::BandwidthAware,400,100),54012.6);
    near(run(ScheduleMode::CompletionTimeAware,800,800),30012.6);
    near(run(ScheduleMode::BandwidthAware,800,800),35014);
    FastAll2All original(&matrix,1),same(&matrix,1);
    original.to_scaled_doubly_stochastic_matrix();same.to_scaled_doubly_stochastic_matrix();
    original.decompose();
    Topology top(6,1,{0,0,1,1,2,2});
    same.decompose(ScheduleMode::Native,top,std::vector<uint64_t>(data.begin(),data.end()));
    assert(original.p_sets.size()==same.p_sets.size());
    for(size_t k=0;k<same.p_sets.size();++k) {
        assert(original.p_sets[k].mp==same.p_sets[k].mp);
        assert(original.p_sets[k].get_freq()==same.p_sets[k].get_freq());
    }
    rejects([&]{same.decompose(ScheduleMode::BandwidthAware,top,{});});
}
static void global_tests() {
    std::vector<uint> data(36,100000000);for(unsigned s=0;s<6;++s)data[s*6+s]=0;
    Topology top(6,1,{0,0,1,1,2,2});
    auto run=[&](ScheduleMode mode,bool configured,pipeline_hooks_t* hooks) {
        std::vector<std::unique_ptr<LocalScheduler>> owners;
        std::vector<LocalScheduler*> locals;
        for(unsigned s=0;s<6;++s){owners.emplace_back(new LocalScheduler(data.data()+s*6,1,6,s,H100));locals.push_back(owners.back().get());}
        GlobalScheduler global(6,1,locals,ETHER400,H100,false,1e-6);
        if(configured)global.set_schedule(mode,&top);
        return global.pipeline2(1e-6,&balance_alpha_beta,hooks);
    };
    near(run(ScheduleMode::Native,false,nullptr).t,11003.5);
    near(run(ScheduleMode::Native,true,nullptr).t,81014);
    near(run(ScheduleMode::BandwidthAware,true,nullptr).t,54012.6);
    pipeline_hooks_t observations;
    near(run(ScheduleMode::BandwidthAware,true,&observations).t,54012.6);
    assert(observations.stages.size()==5&&observations.payload_units.size()==5);
    pipeline_hooks_t conflict;conflict.communication=[](const PermutationSet&,const std::vector<uint64_t>&){return 0.;};
    rejects([&]{run(ScheduleMode::Native,true,&conflict);});
    conflict.communication={};conflict.prepare=[](Matrix&,const std::vector<uint64_t>&,FastAll2All&){};
    rejects([&]{run(ScheduleMode::Native,true,&conflict);});
    // Empty and intra-only data must keep local execution but no network cost.
    for(bool intra:{false,true})for(auto mode:{ScheduleMode::Native,ScheduleMode::BandwidthAware,ScheduleMode::CompletionTimeAware}) {
        std::vector<uint> local_data(16,0);if(intra)local_data[1]=1000000;
        LocalScheduler a(local_data.data(),2,2,0,H100),b(local_data.data()+8,2,2,1,H100);
        GlobalScheduler global(2,2,{&a,&b},ETHER400,H100,false,1e-6);
        Topology t(2,2,{0,1});global.set_schedule(mode,&t);
        near(global.pipeline2(1e-6).t,intra?2.4:0);
    }
    LocalScheduler a(data.data(),1,6,0,H100);
    std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<6;++s){owners.emplace_back(new LocalScheduler(data.data()+s*6,1,6,s,H100));locals.push_back(owners.back().get());}
    GlobalScheduler global(6,1,locals,ETHER400,H100,false,1e-6);
    rejects([&]{global.set_schedule(ScheduleMode::BandwidthAware);});
    global.set_schedule(ScheduleMode::Native,&top);
    rejects([&]{global.pipeline();});rejects([&]{global.pipeline3();});
}
static bool factor_three(uint,uint,scale_info_t info,link_info_t,link_info_t,double) {
    return info.factor>=3;
}
static void units_and_gpu_routes_tests() {
    // Each channel has its own fixed route; two channels partially share resource 0.
    std::vector<ResourceInfo> resources={{"shared",100},{"private",200}};
    std::vector<PathInfo> paths(16);
    paths[2]={{0},5,10}; paths[7]={{0,1},7,14};
    paths[8]={{1},2,4}; paths[13]={{1},3,6};
    auto top=Topology::from_fixed_routes(2,2,resources,paths);
    std::vector<uint> values={0,8,11,0}; Matrix matrix(values.data(),2);
    std::vector<uint64_t> input={0,0,8,5,11,2,0,0};
    for(bool scaled:{false,true})for(auto mode:{ScheduleMode::Native,ScheduleMode::BandwidthAware,ScheduleMode::CompletionTimeAware}) {
        FastAll2All app(&matrix,2,ETHER400,H100,.5e-6); // half a byte per unit
        if(scaled)app.to_scaled_matrix(&factor_three);
        app.to_scaled_doubly_stochastic_matrix();app.decompose(mode,top,input);
        assert(app.verify_decomposition());assert(app.p_sets.size()<=4);
        auto left=input;double transmitted=0;
        for(const auto& stage:app.p_sets) {
            if(scaled)assert(stage.get_freq()%3==0);
            std::vector<GpuTransfer> flows;
            for(uint s=0;s<2;++s)for(uint u=0;u<2;++u) {
                uint d=stage.mp.at(s);auto& pending=left[(s*2+d)*2+u];
                auto take=std::min<uint64_t>(pending,stage.get_freq());pending-=take;
                if(s!=d&&take)flows.push_back({s*2+u,d*2+u,.5*double(take)});
            }
            auto cost=top.evaluate_gpu(flows);
            for(const auto& flow:flows)transmitted+=flow.bytes;
            assert(std::isfinite(cost.total_us));
        }
        for(auto v:left)assert(v==0);near(transmitted,13);
    }
    // New entry rejects inconsistent input, without relaxing the legacy API.
    auto invalid=[&](ScheduleMode mode,double mbpu,std::vector<uint64_t> pending) {
        FastAll2All app(&matrix,2,ETHER400,H100,mbpu);
        app.to_scaled_doubly_stochastic_matrix();
        rejects([&]{app.decompose(mode,top,pending);});
    };
    invalid(static_cast<ScheduleMode>(99),1e-6,input);
    invalid(ScheduleMode::Native,0,input);
    invalid(ScheduleMode::Native,std::numeric_limits<double>::infinity(),input);
    auto bad=input;bad[2]=uint64_t(UINT32_MAX)+1;
    invalid(ScheduleMode::Native,1e-6,bad);
    invalid(ScheduleMode::Native,1e308,input);
    bad=input;bad[2]=100; // cannot deliver more real demand than matrix capacity
    invalid(ScheduleMode::Native,1e-6,bad);
    paths[2]=PathInfo();auto missing=Topology::from_fixed_routes(2,2,resources,paths);
    FastAll2All app(&matrix,2);app.to_scaled_doubly_stochastic_matrix();
    rejects([&]{app.decompose(ScheduleMode::Native,missing,input);});
}
static void exception_cleanup_tests() {
    for(bool late:{false,true}) {
        uint n=late?4:2;
        std::vector<uint> data(n*n,1);for(uint s=0;s<n;++s)data[s*n+s]=0;
        std::vector<std::unique_ptr<LocalScheduler>> owners;
        std::vector<LocalScheduler*> locals;
        for(uint s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*n,1,n,s,H100));locals.push_back(owners.back().get());}
        std::vector<PathInfo> paths(n*n);
        std::vector<ResourceInfo> resources={{"shared",100}};
        if(late)for(uint s=0;s<n;++s)for(uint d=0;d<n;++d)if(s!=d)paths[s*n+d]={{0},0,0};
        auto top=Topology::from_fixed_routes(n,1,resources,paths);
        double mbpu=late?1e302:1e-6;
        GlobalScheduler global(n,1,locals,ETHER400,H100,false,mbpu);
        global.set_schedule(ScheduleMode::Native,&top);
        size_t before=live_arrays;
        rejects([&]{global.pipeline2(mbpu);}); // missing route, or overflow after dispatch
        assert(live_arrays==before);
    }
}
static void completion_shared_acceptance_test() {
    const unsigned n=4;
    std::vector<uint> values(n*n,0);
    for(unsigned s=0;s<n;++s) {
        values[s*n+(s+1)%n]=1000;
        values[s*n+(s+2)%n]=1000;
    }
    Matrix matrix(values.data(),n);
    FastAll2All native(&matrix,1,ETHER400,H100,1e-6);
    native.to_scaled_doubly_stochastic_matrix();native.decompose();
    const auto seed=native.p_sets.front().mp;
    std::vector<ResourceInfo> resources={{"candidate-shared",16}};
    for(unsigned s=0;s<n;++s)resources.push_back({"seed-private-"+std::to_string(s),8});
    std::vector<PathInfo> paths(n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)
        paths[s*n+d]={{seed.at(s)==d?s+1:0},0,0};
    auto top=Topology::from_fixed_routes(n,1,resources,paths);
    FastAll2All aware(&matrix,1,ETHER400,H100,1e-6);
    aware.to_scaled_doubly_stochastic_matrix();
    aware.decompose(ScheduleMode::CompletionTimeAware,top,std::vector<uint64_t>(values.begin(),values.end()));
    std::vector<GpuTransfer> flows;
    for(unsigned s=0;s<n;++s)flows.push_back({s,aware.p_sets.front().mp.at(s),1000});
    double actual=top.evaluate_gpu(flows).total_us;
    std::cout<<"completion shared acceptance: seed=1us selected="<<actual<<"us\n";
    // Candidate pair proxy is .5us, but its four shared flows take 2us, worse than seed1us.
    near(actual,1);
    assert(aware.p_sets.front().mp==seed);
}
static void mixed_intrinsic_self_loop_test() {
    const unsigned n=3,g=2,ranks=n*g;
    std::vector<uint> data(ranks*ranks,0);
    for(unsigned u=0;u<g;++u) {
        data[u*ranks+g+u]=200000000;
        data[(g+u)*ranks+u]=200000000;
    }
    data[(2*g)*ranks+2*g+1]=100000000;
    for(bool cross_dispatch:{false,true})for(auto mode:{ScheduleMode::Native,ScheduleMode::BandwidthAware,ScheduleMode::CompletionTimeAware}) {
        data[g]=cross_dispatch?0:200000000;
        data[g+1]=cross_dispatch?200000000:0;
        std::vector<std::unique_ptr<LocalScheduler>> owners;
        std::vector<LocalScheduler*> locals;
        for(unsigned s=0;s<n;++s) {
            owners.emplace_back(new LocalScheduler(data.data()+s*g*ranks,g,n,s,H100));
            locals.push_back(owners.back().get());
        }
        GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
        Topology top(n,g,{0,1,2});scheduler.set_schedule(mode,&top);
        pipeline_hooks_t hooks;scheduler.pipeline2(1e-6,&balance_alpha_beta,&hooks);
        assert(hooks.stages.size()==1);
        near(hooks.intrinsic_us,230.1);
        std::cout<<"mixed intrinsic: intrinsic="<<hooks.intrinsic_us
                 <<" dispatch="<<hooks.dispatch_us[0]
                 <<" self-payload="<<hooks.payload_units[0][4]<<"\n";
        near(hooks.dispatch_us[0],cross_dispatch?460.1:0);
        assert(hooks.payload_units[0][4]==0&&hooks.payload_units[0][5]==0);
        assert(locals[2]->pending_units(2,0)==100000000);
        assert(locals[2]->get_intrinsic_all2all()[1]==100000000);
        // approx读取row_sum；若self-dispatch曾从零槽位减去intrinsic会得到UINT下溢值。
        locals[2]->server2server_approx(2);
        assert(locals[2]->server2server_data[2]==0);
    }
}
static void bandwidth_only_semantics_test() {
    const unsigned n=4;
    std::vector<uint> values(n*n,0);
    for(unsigned s=0;s<n;++s) {
        values[s*n+(s+1)%n]=1000;values[s*n+(s+2)%n]=1000;
    }
    Matrix matrix(values.data(),n);
    FastAll2All native(&matrix,1,ETHER400,H100,1e-6);
    native.to_scaled_doubly_stochastic_matrix();native.decompose();
    auto seed=native.p_sets.front().mp;
    std::vector<ResourceInfo> resources={{"seed-shared",8}};
    for(unsigned s=0;s<n;++s)resources.push_back({"candidate-private-"+std::to_string(s),16});
    std::vector<PathInfo> paths(n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)
        paths[s*n+d]=seed.at(s)==d?PathInfo({0},0,0):PathInfo({s+1},100,200);
    auto top=Topology::from_fixed_routes(n,1,resources,paths);
    FastAll2All aware(&matrix,1,ETHER400,H100,1e-6);
    aware.to_scaled_doubly_stochastic_matrix();
    aware.decompose(ScheduleMode::BandwidthAware,top,std::vector<uint64_t>(values.begin(),values.end()));
    std::vector<GpuTransfer> flows;
    for(unsigned s=0;s<n;++s)flows.push_back({s,aware.p_sets.front().mp.at(s),1000});
    auto cost=top.evaluate_gpu(flows);
    near(cost.serial_us,.5);near(cost.alpha_us,100);near(cost.total_us,100.5);
    std::cout<<"bandwidth-only semantics: seed total4us, selected serial="<<cost.serial_us
             <<"us total="<<cost.total_us<<"us\n";
}
static void model_boundary_tests() {
    std::vector<ResourceInfo> resources={{"a-fast",8},{"a-slow",.8},{"b1",1},{"b2",1}};
    std::vector<PathInfo> paths(16);
    paths[1]={{0},10,20};paths[11]={{1},1,2};
    paths[3]={{2},5,10};paths[9]={{3},5,10};
    auto independent=Topology::from_fixed_routes(4,1,resources,paths);
    near(independent.evaluate_gpu({{0,1,1000},{2,3,1000}}).total_us,20);
    near(independent.evaluate_gpu({{0,3,1000},{2,1,1000}}).total_us,13);
    // Independent true max(path propagation+serialization) is11us for A and13us for B.
    near(std::max(10.+1,1.+10),11);
    paths[1].rtt_us=200000;paths[11].rtt_us=700000;
    auto changed_rtt=Topology::from_fixed_routes(4,1,resources,paths);
    near(changed_rtt.evaluate_gpu({{0,1,1000},{2,3,1000}}).total_us,20);
    resources={{"shared",1}};paths.assign(16,PathInfo());
    paths[1]={{0},5,10};paths[11]={{0},5,10};
    auto shared=Topology::from_fixed_routes(4,1,resources,paths);
    near(shared.evaluate_gpu({{0,1,1000},{2,3,1000}}).total_us,21);
    resources={{"gateway",8},{"wan",.8}};
    paths[1]={{0,1},5,10};paths[11]={{0,1},5,10};
    auto multilevel=Topology::from_fixed_routes(4,1,resources,paths);
    near(multilevel.evaluate_gpu({{0,1,1000},{2,3,1000}}).total_us,25);
    // Whole-message store-forward reference: first gateway1us + two WAN services20us +5us.
    // This reference is26us, not a claim that the current fluid approximation is exact.
    near(1.+20.+5,26);
}
static void complete_matching_example_test() {
    const std::vector<uint> values = {1, 1, 2, 1, 2, 1, 2, 1, 1};
    for (auto strategy :
         {DecompositionStrategy::MaximumBottleneck, DecompositionStrategy::MaximumElimination,
          DecompositionStrategy::ResidualDegree}) {
        Matrix matrix(const_cast<uint *>(values.data()), 3);
        FastAll2All app(&matrix, 1);
        app.to_scaled_doubly_stochastic_matrix();
        DecompositionOptions options;
        options.strategy = strategy;
        DecompositionStats stats;
        app.decompose(options, &stats);
        assert(app.p_sets.size() == 3);
        assert(stats.decomposition_steps == 3 && stats.steps.size() == 3);
        assert(stats.steps.front().q == 2 && stats.steps.front().eliminated == 3);
        assert(stats.steps.front().degree_before == 3 && stats.steps.front().degree_after == 2);
        assert(stats.cpu_us >= 0 && stats.cpu_tick_us > 0);
        assert(app.verify_decomposition());
    }
    Matrix matrix(const_cast<uint *>(values.data()), 3);
    FastAll2All forced(&matrix, 1);
    forced.to_scaled_doubly_stochastic_matrix();
    unsigned call = 0;
    forced.decompose_with([&](Matrix &, hungarian_info_t &state) {
        if (call++ == 0)
            for (unsigned s = 0; s < 3; ++s) {
                state.matching[s] = 3 + s;
                state.matching[3 + s] = s;
            }
    });
    assert(forced.p_sets.size() == 4 && forced.verify_decomposition());
}
struct MatchingOracle {
    uint bottleneck = 0;
    int eliminations = -1;
    bool restricted = false;
};
static unsigned maximum_degree(const std::vector<uint> &residual, unsigned n) {
    unsigned maximum = 0;
    for (unsigned s = 0; s < n; ++s) {
        unsigned row = 0, col = 0;
        for (unsigned d = 0; d < n; ++d) {
            row += residual[s * n + d] > 0;
            col += residual[d * n + s] > 0;
        }
        maximum = std::max(maximum, std::max(row, col));
    }
    return maximum;
}
static MatchingOracle matching_oracle(const std::vector<uint> &residual, unsigned n,
                                      uint threshold) {
    // Exhaustive permutations are TEST ONLY, n<=5; never used by the online solver.
    std::vector<unsigned> perm(n), rows(n, 0), cols(n, 0);
    std::iota(perm.begin(), perm.end(), 0);
    unsigned delta = maximum_degree(residual, n);
    for (unsigned s = 0; s < n; ++s)
        for (unsigned d = 0; d < n; ++d)
            if (residual[s * n + d]) {
                ++rows[s];
                ++cols[d];
            }
    MatchingOracle result;
    do {
        uint q = UINT32_MAX;
        int count = 0;
        bool restricted = true;
        for (unsigned s = 0; s < n; ++s) {
            uint value = residual[s * n + perm[s]];
            q = std::min(q, value);
            count += value == threshold;
            if ((rows[s] == delta || cols[perm[s]] == delta) && value != threshold)
                restricted = false;
        }
        result.bottleneck = std::max(result.bottleneck, q);
        if (q >= threshold) {
            result.eliminations = std::max(result.eliminations, count);
            result.restricted = result.restricted || restricted;
        }
    } while (std::next_permutation(perm.begin(), perm.end()));
    return result;
}
static void audit_complete_matching(std::vector<uint> residual, unsigned n, const FastAll2All &app,
                                    const DecompositionOptions &options,
                                    const DecompositionStats &stats) {
    assert(stats.steps.size() == app.p_sets.size());
    assert(stats.decomposition_steps == app.p_sets.size());
    uint64_t probes = 0, weighted = 0;
    for (size_t k = 0; k < app.p_sets.size(); ++k) {
        const auto &stage = app.p_sets[k];
        const auto &step = stats.steps[k];
        assert(stage.mp.size() == n);
        std::set<unsigned> used;
        uint q = UINT32_MAX;
        unsigned eliminated = 0;
        for (unsigned s = 0; s < n; ++s) {
            auto d = stage.mp.at(s);
            assert(d < n && used.insert(d).second);
            q = std::min(q, residual[s * n + d]);
        }
        assert(q > 0 && q == step.q_scaled && q == stage.get_freq());
        assert(step.q == stage.get_freq() && step.degree_before == maximum_degree(residual, n));
        auto oracle = matching_oracle(residual, n, 1);
        if (options.strategy == DecompositionStrategy::MaximumBottleneck)
            assert(q == oracle.bottleneck);
        if (options.strategy == DecompositionStrategy::MaximumElimination) {
            assert(step.weighted_calls == step.sampled_thresholds.size());
            assert(step.weighted_calls <= options.threshold_budget);
            assert(step.sampled_eliminations.size() == step.sampled_thresholds.size());
            int best = -1;
            uint best_q = 0;
            for (size_t i = 0; i < step.sampled_thresholds.size(); ++i) {
                uint t = step.sampled_thresholds[i];
                assert(t > 0 && t <= oracle.bottleneck);
                int count = matching_oracle(residual, n, t).eliminations;
                assert(count >= 0 && step.sampled_eliminations[i] == unsigned(count));
                if (count > 0 && (count > best || (count == best && t > best_q))) {
                    best = count;
                    best_q = t;
                }
            }
            assert(q == best_q && step.selected_threshold == best_q);
        }
        if (options.strategy == DecompositionStrategy::ResidualDegree) {
            uint selected = 0;
            for (uint t : step.sampled_thresholds) {
                assert(t > 0 && t <= oracle.bottleneck);
                if (matching_oracle(residual, n, t).restricted)
                    selected = std::max(selected, t);
            }
            assert(step.weighted_calls == 0 &&
                   step.sampled_thresholds.size() <= options.threshold_budget);
            assert(step.degree_fallback == (selected == 0));
            assert(q == (selected ? selected : oracle.bottleneck));
        }
        if (options.strategy == DecompositionStrategy::MaximumElimination ||
            options.strategy == DecompositionStrategy::ResidualDegree) {
            std::set<uint> samples(step.sampled_thresholds.begin(), step.sampled_thresholds.end());
            assert(samples.size() == step.sampled_thresholds.size());
            assert(samples.count(oracle.bottleneck));
            if (options.threshold_budget > 1) {
                uint minimum = UINT32_MAX;
                for (uint value : residual)
                    if (value)
                        minimum = std::min(minimum, value);
                assert(samples.count(minimum));
            }
        }
        for (unsigned s = 0; s < n; ++s) {
            auto &value = residual[s * n + stage.mp.at(s)];
            eliminated += value == q;
            value -= q;
        }
        assert(step.eliminated == eliminated && eliminated > 0);
        assert(step.degree_after == maximum_degree(residual, n));
        assert(step.degree_after <= step.degree_before);
        if (options.strategy == DecompositionStrategy::ResidualDegree && !step.degree_fallback)
            assert(step.degree_after + 1 == step.degree_before);
        if (options.strategy == DecompositionStrategy::MaximumElimination) {
            unsigned best = *std::max_element(step.sampled_eliminations.begin(),
                                              step.sampled_eliminations.end());
            assert(step.eliminated == best);
        }
        assert(step.cpu_us >= 0);
        probes += step.threshold_probes;
        weighted += step.weighted_calls;
    }
    for (auto value : residual)
        assert(value == 0);
    assert(stats.threshold_probes == probes && stats.weighted_calls == weighted);
}
static void complete_matching_oracle_tests() {
    std::mt19937 random(20261111);
    for (unsigned n = 1; n <= 5; ++n)
        for (unsigned repeat = 0; repeat < 12; ++repeat) {
            std::vector<uint> values(n * n, 0), permutation(n);
            std::iota(permutation.begin(), permutation.end(), 0);
            for (unsigned r = 0; r < 7; ++r) {
                std::shuffle(permutation.begin(), permutation.end(), random);
                uint weight = 1 + random() % 4;
                for (unsigned s = 0; s < n; ++s)
                    values[s * n + permutation[s]] += weight;
            }
            for (auto strategy :
                 {DecompositionStrategy::Kuhn, DecompositionStrategy::MaximumBottleneck,
                  DecompositionStrategy::MaximumElimination, DecompositionStrategy::ResidualDegree})
                for (unsigned budget : {1u, 4u, 8u}) {
                    Matrix matrix(values.data(), n);
                    FastAll2All app(&matrix, 1);
                    app.to_scaled_doubly_stochastic_matrix();
                    DecompositionOptions options;
                    options.strategy = strategy;
                    options.threshold_budget = budget;
                    DecompositionStats stats;
                    app.decompose(options, &stats);
                    audit_complete_matching(values, n, app, options, stats);
                    assert(app.verify_decomposition());
                }
        }
}
static void complete_matching_pipeline_test() {
    const unsigned n = 4, g = 2, ranks = n * g;
    std::vector<uint> data(ranks * ranks, 0);
    for (unsigned s = 0; s < n; ++s)
        for (unsigned d = 0; d < n; ++d)
            if (s != d)
                for (unsigned u = 0; u < g; ++u)
                    data[(s * g + u) * ranks + d * g + (1 - u)] = 1 + (s * 3 + d) % 5;
    auto run = [&](DecompositionStrategy strategy, bool topology, bool configure) {
        std::vector<std::unique_ptr<LocalScheduler>> owners;
        std::vector<LocalScheduler *> locals;
        for (unsigned s = 0; s < n; ++s) {
            owners.emplace_back(new LocalScheduler(data.data() + s * g * ranks, g, n, s, H100));
            locals.push_back(owners.back().get());
        }
        GlobalScheduler scheduler(n, g, locals, ETHER400, H100, false, 1e-6);
        Topology top(n, g, {0, 0, 1, 1});
        if (topology)
            scheduler.set_schedule(ScheduleMode::Native, &top);
        if (configure) {
            DecompositionOptions options;
            options.strategy = strategy;
            scheduler.set_decomposition(options);
        }
        pipeline_hooks_t hooks;
        scheduler.pipeline2(1e-6, &balance_alpha_beta, &hooks);
        uint64_t sent = 0;
        for (const auto &row : hooks.payload_units)
            for (auto value : row)
                sent += value;
        uint64_t wanted = 0;
        for (auto value : data)
            wanted += value;
        assert(sent == wanted);
        for (unsigned s = 0; s < n; ++s)
            for (unsigned d = 0; d < n; ++d)
                if (s != d)
                    for (unsigned u = 0; u < g; ++u)
                        assert(locals[s]->pending_units(d, u) == 0);
        if (configure) {
            const auto &stats = scheduler.decomposition_stats();
            assert(stats.decomposition_steps == hooks.stages.size());
            assert(hooks.decomposition_stats.steps.size() == stats.steps.size());
            assert(stats.cpu_us >= 0 && stats.cpu_tick_us > 0);
        }
        return hooks;
    };
    for (bool topology : {false, true}) {
        auto legacy = run(DecompositionStrategy::Kuhn, topology, false);
        for (auto strategy :
             {DecompositionStrategy::Kuhn, DecompositionStrategy::MaximumBottleneck,
              DecompositionStrategy::MaximumElimination, DecompositionStrategy::ResidualDegree}) {
            auto actual = run(strategy, topology, true);
            if (strategy == DecompositionStrategy::Kuhn) {
                assert(actual.stages.size() == legacy.stages.size());
                assert(actual.payload_units == legacy.payload_units);
                assert(actual.dispatch_us == legacy.dispatch_us);
                for (size_t k = 0; k < actual.stages.size(); ++k) {
                    assert(actual.stages[k].mp == legacy.stages[k].mp);
                    assert(actual.stages[k].get_freq() == legacy.stages[k].get_freq());
                }
            }
        }
    }
    std::vector<std::unique_ptr<LocalScheduler>> owners;
    std::vector<LocalScheduler *> locals;
    for (unsigned s = 0; s < n; ++s) {
        owners.emplace_back(new LocalScheduler(data.data() + s * g * ranks, g, n, s, H100));
        locals.push_back(owners.back().get());
    }
    GlobalScheduler scheduler(n, g, locals, ETHER400, H100, false, 1e-6);
    Topology top(n, g, {0, 0, 1, 1});
    DecompositionOptions options;
    options.strategy = DecompositionStrategy::MaximumElimination;
    scheduler.set_decomposition(options);
    rejects([&] { scheduler.set_schedule(ScheduleMode::BandwidthAware, &top); });
    rejects([&] { scheduler.pipeline(); });
    rejects([&] { scheduler.pipeline3(); });
    pipeline_hooks_t overriding;
    overriding.prepare = [](Matrix &, const std::vector<uint64_t> &, FastAll2All &) {};
    rejects([&] { scheduler.pipeline2(1e-6, &balance_alpha_beta, &overriding); });
    overriding.prepare = {};
    overriding.communication = [](const PermutationSet &, const std::vector<uint64_t> &) {
        return 0.;
    };
    rejects([&] { scheduler.pipeline2(1e-6, &balance_alpha_beta, &overriding); });
    overriding.communication = {};
    overriding.defer_decomposition = true;
    rejects([&] { scheduler.pipeline2(1e-6, &balance_alpha_beta, &overriding); });
    GlobalScheduler reverse(n, g, locals, ETHER400, H100, false, 1e-6);
    reverse.set_schedule(ScheduleMode::CompletionTimeAware, &top);
    rejects([&] { reverse.set_decomposition(options); });
}
static void complete_matching_boundaries_test() {
    const std::vector<std::vector<uint>> fixtures = {
        {0, 0, 0, 0, 0, 0, 0, 0, 0},
        {0, 0, 1, 2, 0, 0, 0, 0, 0},
        {1, 2, 3, 2, 2, 2, 3, 2, 1}, // No all-critical equal-t perfect matching: S3 fallback.
        {4, 2, 1, 1, 5, 1, 2, 0, 5}
        // t=2 is feasible, but no t=2 edge lies in an eligible assignment.
    };
    for (auto values : fixtures) {
        Matrix matrix(values.data(), 3);
        FastAll2All native(&matrix, 1);
        native.to_scaled_doubly_stochastic_matrix();
        native.decompose();
        std::vector<uint> padded(9, 0);
        for (const auto &stage : native.p_sets)
            for (const auto &edge : stage.mp)
                padded[edge.first * 3 + edge.second] += stage.get_freq();
        for (auto strategy :
             {DecompositionStrategy::Kuhn, DecompositionStrategy::MaximumBottleneck,
              DecompositionStrategy::MaximumElimination, DecompositionStrategy::ResidualDegree}) {
            DecompositionOptions options;
            options.strategy = strategy;
            DecompositionStats stats;
            FastAll2All app(&matrix, 1);
            app.to_scaled_doubly_stochastic_matrix();
            app.decompose(options, &stats);
            audit_complete_matching(padded, 3, app, options, stats);
            if (strategy == DecompositionStrategy::Kuhn) {
                assert(app.p_sets.size() == native.p_sets.size());
                for (size_t k = 0; k < app.p_sets.size(); ++k) {
                    assert(app.p_sets[k].mp == native.p_sets[k].mp);
                    assert(app.p_sets[k].get_freq() == native.p_sets[k].get_freq());
                }
            }
            if (values == fixtures[2] && strategy == DecompositionStrategy::ResidualDegree)
                assert(stats.steps.front().degree_fallback);
            if (values == fixtures[3] && strategy == DecompositionStrategy::MaximumElimination) {
                const auto &step = stats.steps.front();
                auto it =
                    std::find(step.sampled_thresholds.begin(), step.sampled_thresholds.end(), 2);
                assert(it != step.sampled_thresholds.end());
                assert(step.sampled_eliminations[it - step.sampled_thresholds.begin()] == 0);
                assert(step.selected_threshold != 2);
            }
            FastAll2All no_stats(&matrix, 1);
            no_stats.to_scaled_doubly_stochastic_matrix();
            no_stats.decompose(options);
            assert(no_stats.verify_decomposition());
        }
    }
    std::vector<uint> values = {0, 8, 11, 0};
    Matrix matrix(values.data(), 2);
    for (auto strategy :
         {DecompositionStrategy::Kuhn, DecompositionStrategy::MaximumBottleneck,
          DecompositionStrategy::MaximumElimination, DecompositionStrategy::ResidualDegree}) {
        FastAll2All app(&matrix, 1);
        app.to_scaled_matrix(&factor_three);
        app.to_scaled_doubly_stochastic_matrix();
        DecompositionOptions options;
        options.strategy = strategy;
        DecompositionStats stats;
        app.decompose(options, &stats);
        for (size_t k = 0; k < app.p_sets.size(); ++k) {
            assert(stats.steps[k].q == app.p_sets[k].get_freq());
            assert(stats.steps[k].q == 3 * stats.steps[k].q_scaled);
        }
        assert(app.verify_decomposition());
    }
    FastAll2All invalid(&matrix, 1);
    DecompositionOptions options;
    rejects([&] { invalid.decompose(options); });
    invalid.to_scaled_doubly_stochastic_matrix();
    options.threshold_budget = 0;
    rejects([&] { invalid.decompose(options); });
    options.threshold_budget = 8;
    options.strategy = static_cast<DecompositionStrategy>(99);
    rejects([&] { invalid.decompose(options); });
}
int main() {
    complete_matching_example_test();
    complete_matching_oracle_tests();
    complete_matching_pipeline_test();
    complete_matching_boundaries_test();
    model_boundary_tests();
    bandwidth_only_semantics_test();
    mixed_intrinsic_self_loop_test();
    completion_shared_acceptance_test();
    topology_tests();
    a2a_tests();
    global_tests();
    units_and_gpu_routes_tests();
    exception_cleanup_tests();
    std::cout << "Topology schedule tests passed\n";
}
