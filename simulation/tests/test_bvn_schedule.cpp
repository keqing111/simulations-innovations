#include "algorithm/all2all.h"
#include "algorithm/bvn_schedule.h"
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <unistd.h>

using namespace bvn;
static unsigned assertions = 0;
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        ++assertions;                                                                              \
        if (!(x))                                                                                  \
            throw std::runtime_error("check failed: " #x);                                         \
    } while (0)
template <class F> void rejects(F f) {
    bool bad = false;
    try {
        f();
    } catch (const std::exception &) {
        bad = true;
    }
    CHECK(bad);
}
Topology topology(unsigned n, bool shared = false, double bw = 100, double latency = 5) {
    std::vector<native_topology::ResourceInfo> rs;
    std::vector<native_topology::PathInfo> ps(n * n);
    rs.push_back({"shared", bw});
    for (unsigned s = 0; s < n; ++s)
        for (unsigned d = 0; d < n; ++d)
            if (s != d) {
                unsigned id = 0;
                if (!shared) {
                    id = rs.size();
                    rs.push_back({"r" + std::to_string(id), bw});
                }
                ps[s * n + d] = native_topology::PathInfo({id}, latency, 2 * latency);
            }
    return Topology::from_fixed_routes(n, 1, rs, ps);
}
void conserved(const Demand &d, const Topology &t, Options o = Options()) {
    Plan p = plan(d, t, o);
    CHECK(validate_schedule(d, p).ok);
    CHECK(p.stages.size() <= d.bytes.size());
    CHECK(p.candidate_evaluations <= p.decomposition_steps * o.candidate_budget);
    CHECK(p.edit_attempts <= p.decomposition_steps * o.edit_attempt_budget);
    CHECK(std::isfinite(evaluate_schedule(t, p).communication_us));
}
void noop_configuration_test() {
    Demand demand{2, {0, 1, 2, 0}};
    auto top = topology(2);
    for (unsigned budget : {1u, 8u}) {
        Options options;
        options.global = GlobalRule::Pressure;
        options.local = LocalRule::StageTime;
        options.candidate_budget = budget;
        rejects([&] { plan(demand, top, options); });
    }
}
void basic() {
    Demand d{3, {0, 0, 1, 2, 0, 0, 0, 0, 0}};
    auto t = topology(3);
    Plan p = plan(d, t, Options());
    CHECK(p.stages.size() == 2);
    CHECK(validate_schedule(d, p).ok);
    CHECK(p.stages[0].flows.size() == 2);
    CHECK(p.stages[0].coefficient == 1);
    Options native;
    native.engine = Engine::FastNative;
    Plan a = plan(d, t, native);
    CHECK(a.decomposition_steps == 2);
    CHECK(validate_schedule(d, a).ok);
    unsigned raw[] = {0, 0, 1, 2, 0, 0, 0, 0, 0};
    Matrix m(raw, 3);
    FastAll2All fast(&m, 1, INFB, DGX2, 1e-6);
    fast.to_scaled_doubly_stochastic_matrix();
    fast.decompose();
    CHECK(a.decomposition_steps == fast.p_sets.size());
    native.native_unit_bytes = 1024;
    Demand scaled{3, {0, 0, 1024, 2048, 0, 0, 0, 0, 0}};
    Plan unit_plan = plan(scaled, t, native);
    CHECK(validate_schedule(scaled, unit_plan).ok);
    CHECK(unit_plan.stages.size() == a.stages.size());
    for (size_t stage = 0; stage < a.stages.size(); ++stage) {
        CHECK(unit_plan.stages[stage].coefficient == a.stages[stage].coefficient * 1024);
        for (size_t flow = 0; flow < a.stages[stage].flows.size(); ++flow)
            CHECK(unit_plan.stages[stage].flows[flow].bytes ==
                  a.stages[stage].flows[flow].bytes * 1024);
    }
    std::vector<uint64_t> rem = d.bytes;
    size_t k = 0;
    for (const auto &set : fast.p_sets) {
        std::vector<Flow> flows;
        for (const auto &e : set.mp) {
            auto &r = rem[e.first * 3 + e.second];
            uint64_t x = std::min<uint64_t>(r, set.get_freq());
            r -= x;
            if (x && e.first != e.second)
                flows.push_back({e.first, e.second, double(x)});
        }
        if (flows.empty())
            continue;
        CHECK(a.stages[k].coefficient == set.get_freq());
        CHECK(a.stages[k].flows.size() == flows.size());
        for (size_t i = 0; i < flows.size(); ++i) {
            CHECK(a.stages[k].flows[i].src_gpu == flows[i].src_gpu);
            CHECK(a.stages[k].flows[i].dst_gpu == flows[i].dst_gpu);
            CHECK(a.stages[k].flows[i].bytes == flows[i].bytes);
        }
        ++k;
    }
    conserved(Demand{1, {0}}, topology(1));
    conserved(Demand{3, std::vector<uint64_t>(9, 0)}, t);
    Plan zero = plan(Demand{1, {0}}, topology(1), native);
    CHECK(zero.decomposition_steps == 0);
    CHECK(evaluate_schedule(topology(1), zero).communication_us == 0);
    Demand one{2, {0, 1024, 0, 0}};
    auto p1 = plan(one, topology(2), Options());
    CHECK(std::abs(evaluate_schedule(topology(2), p1).communication_us - 5.08192) < 1e-12);
    Demand two{4, std::vector<uint64_t>(16, 0)};
    two.bytes[1] = 1000;
    two.bytes[11] = 1000;
    CHECK(std::abs(evaluate_schedule(topology(4, true), plan(two, topology(4, true), Options()))
                       .communication_us -
                   5.16) < 1e-12);
    CHECK(std::abs(
              evaluate_schedule(topology(4), plan(two, topology(4), Options())).communication_us -
              5.08) < 1e-12);
    Demand reverse{2, {0, 1000, 1000, 0}};
    CHECK(
        std::abs(
            evaluate_schedule(topology(2), plan(reverse, topology(2), Options())).communication_us -
            5.08) < 1e-12);
}
void invalid() {
    Demand d{2, {0, 1, 2, 0}};
    auto t = topology(2);
    Options o;
    rejects([&] { plan(Demand{2, {1, 0, 0, 0}}, t, o); });
    rejects([&] { plan(Demand{2, {0, 1}}, t, o); });
    rejects([&] { plan(Demand{2, {0, (uint64_t(1) << 53), 1, 0}}, t, o); });
    rejects([&] { plan(d, topology(3), o); });
    o.candidate_budget = 0;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.local_edits = true;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.pressure_candidate = true;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.global = GlobalRule::SmoothResidual;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.delay_alpha = 1;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.native_unit_bytes = 2;
    rejects([&] { plan(d, t, o); });
    o.engine = Engine::FastNative;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.engine = Engine::FastNative;
    rejects([&] { plan(Demand{2, {0, uint64_t(INT32_MAX) + 1, 0, 0}}, t, o); });
    conserved(Demand{2, {0, uint64_t(INT32_MAX), 0, 0}}, t, o);
    Demand row_limit{
        4, {0, uint64_t(INT32_MAX), uint64_t(INT32_MAX), 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}};
    rejects([&] { plan(row_limit, topology(4), o); });
    Demand col_limit{
        4, {0, 0, 0, 0, uint64_t(INT32_MAX), 0, 0, 0, uint64_t(INT32_MAX), 0, 0, 0, 2, 0, 0, 0}};
    rejects([&] { plan(col_limit, topology(4), o); });
    Options bytes;
    conserved(Demand{2, {0, uint64_t(1) << 53, 0, 0}}, t, bytes);
    std::vector<native_topology::PathInfo> paths(4);
    auto missing = Topology::from_fixed_routes(2, 1, {{"r", 100}}, paths);
    rejects([&] { plan(d, missing, Options()); });
    Plan p = plan(d, t, Options());
    Plan bad = p;
    bad.stages[0].flows[0].bytes = .5;
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages[0].flows.push_back(bad.stages[0].flows[0]);
    CHECK(!validate_schedule(d, bad).ok);
    rejects([&] { evaluate_schedule(t, bad); });
    bad = p;
    bad.stages[0].coefficient = 0;
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages[0].flows[0].src_gpu = 9;
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages.push_back(Stage());
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages[0].flows[0].bytes = std::numeric_limits<double>::infinity();
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages[0].flows[0].bytes += 1;
    CHECK(!validate_schedule(d, bad).ok);
    o = Options();
    o.engine = static_cast<Engine>(99);
    rejects([&] { plan(d, t, o); });
    Demand zero_pair{3, {0, 1, 0, 0, 0, 0, 0, 0, 0}};
    o = Options();
    o.space = Space::SerializationTime;
    bad = plan(zero_pair, topology(3), o);
    Stage invented;
    invented.coefficient = 1;
    invented.flows.push_back({1, 2, 1e-8});
    bad.stages.push_back(invented);
    CHECK(!validate_schedule(zero_pair, bad).ok);
    Demand duplicate_source{3, {0, 1, 1, 0, 0, 0, 0, 0, 0}};
    bad = Plan();
    Stage conflicts;
    conflicts.coefficient = 1;
    conflicts.flows = {{0, 1, 1}, {0, 2, 1}};
    bad.stages.push_back(conflicts);
    CHECK(!validate_schedule(duplicate_source, bad).ok);
    CHECK(validate_schedule(duplicate_source, bad).message.find("duplicate") != std::string::npos);
    Demand duplicate_destination{3, {0, 1, 0, 0, 0, 0, 0, 1, 0}};
    bad.stages[0].flows = {{0, 1, 1}, {2, 1, 1}};
    CHECK(!validate_schedule(duplicate_destination, bad).ok);
    CHECK(validate_schedule(duplicate_destination, bad).message.find("duplicate") !=
          std::string::npos);
    bad = p;
    bad.stages[0].flows[0].dst_gpu = bad.stages[0].flows[0].src_gpu;
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages[0].flows[0].bytes = 0;
    CHECK(!validate_schedule(d, bad).ok);
    bad = p;
    bad.stages[0].coefficient = std::numeric_limits<long double>::quiet_NaN();
    CHECK(!validate_schedule(d, bad).ok);
    o = Options();
    o.global = static_cast<GlobalRule>(99);
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.local = static_cast<LocalRule>(99);
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.space = static_cast<Space>(99);
    rejects([&] { plan(d, t, o); });
    for (double invalid : {-1.0, 0.0, 1.1, std::numeric_limits<double>::quiet_NaN()}) {
        o = Options();
        o.min_seed_service_ratio = invalid;
        rejects([&] { plan(d, t, o); });
    }
    o = Options();
    o.edit_attempt_budget = 1;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.candidate_budget = 2;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.smooth_beta = 4;
    rejects([&] { plan(d, t, o); });
    o = Options();
    o.space = Space::DelayAdjustedTime;
    o.delay_alpha = -1;
    rejects([&] { plan(d, t, o); });
}
void objectives() {
    // Equal independent flows have the same stage time together or separately;
    // a tie must not discard half the service and create another identical stage.
    Demand equal{4, std::vector<uint64_t>(16, 0)};
    equal.bytes[1] = equal.bytes[11] = 1000;
    Options equal_options;
    equal_options.local = LocalRule::StageTime;
    equal_options.local_edits = true;
    equal_options.candidate_budget = 8;
    equal_options.edit_attempt_budget = 32;
    equal_options.min_seed_service_ratio = .5;
    Plan equal_plan = plan(equal, topology(4), equal_options);
    CHECK(equal_plan.stages.size() == 1);
    CHECK(equal_plan.stages[0].flows.size() == 2);
    CHECK(std::abs(evaluate_schedule(topology(4), equal_plan).communication_us - 5.08) < 1e-12);
    // Service dominance is exact, not a double-relative score tie: losing one byte
    // from a 1e15-byte candidate must not become a lexicographic "improvement".
    Demand unbalanced{4, std::vector<uint64_t>(16, 0)};
    unbalanced.bytes[1] = 1000000000000000ULL;
    unbalanced.bytes[11] = 1;
    std::vector<native_topology::ResourceInfo> dominance_resources = {{"fast", 1e15}, {"slow", 1}};
    std::vector<native_topology::PathInfo> dominance_paths(16);
    for (unsigned s=0;s<4;++s)for(unsigned d=0;d<4;++d)if(s!=d)
        dominance_paths[s*4+d] = native_topology::PathInfo({0},5,10);
    dominance_paths[11] = native_topology::PathInfo({1},5,10);
    auto dominance_topology = Topology::from_fixed_routes(4,1,dominance_resources,dominance_paths);
    equal_options.space = Space::SerializationTime;
    CHECK(plan(unbalanced, dominance_topology, equal_options).stages.size() == 1);
    Demand d{4, {0, 17, 9, 31, 11, 0, 23, 4, 5, 19, 0, 7, 29, 3, 13, 0}};
    auto t = topology(4, true, 73, 1e-9);
    for (int space = 0; space < 3; ++space)
        for (int global = 0; global < 4; ++global)
            for (int local = 0; local < 4; ++local) {
                Options o;
                o.space = static_cast<Space>(space);
                o.global = static_cast<GlobalRule>(global);
                o.local = static_cast<LocalRule>(local);
                if (space == 2)
                    o.delay_alpha = 1;
                if (global == 2)
                    o.smooth_beta = 4;
                if (global || local) {
                    o.pressure_candidate = true;
                    o.local_edits = true;
                    o.candidate_budget = 12;
                    o.edit_attempt_budget = 64;
                    o.min_seed_service_ratio = .3;
                }
                conserved(d, t, o);
                Plan a = plan(d, t, o), b = plan(d, t, o);
                CHECK(a.stages.size() == b.stages.size());
                CHECK(a.candidate_evaluations == b.candidate_evaluations);
                for (size_t k = 0; k < a.stages.size(); ++k) {
                    CHECK(a.stages[k].coefficient == b.stages[k].coefficient);
                    CHECK(a.stages[k].flows.size() == b.stages[k].flows.size());
                    for (size_t f = 0; f < a.stages[k].flows.size(); ++f) {
                        CHECK(a.stages[k].flows[f].src_gpu == b.stages[k].flows[f].src_gpu);
                        CHECK(a.stages[k].flows[f].dst_gpu == b.stages[k].flows[f].dst_gpu);
                        CHECK(a.stages[k].flows[f].bytes == b.stages[k].flows[f].bytes);
                    }
                }
            }
    Plan bytes = plan(d, t, Options());
    Options o;
    o.space = Space::SerializationTime;
    Plan time = plan(d, t, o);
    o.space = Space::DelayAdjustedTime;
    Plan delay = plan(d, t, o);
    CHECK(bytes.stages.size() == time.stages.size());
    CHECK(time.stages.size() == delay.stages.size());
    for (size_t k = 0; k < bytes.stages.size(); ++k)
        for (size_t f = 0; f < bytes.stages[k].flows.size(); ++f) {
            CHECK(std::abs(bytes.stages[k].flows[f].bytes - time.stages[k].flows[f].bytes) < 1e-9);
            CHECK(time.stages[k].flows[f].bytes == delay.stages[k].flows[f].bytes);
        }
    // Local stage cost can select a lower-cardinality partial matching.
    Demand deletion{4, std::vector<uint64_t>(16, 0)};
    deletion.bytes[1] = 1;
    deletion.bytes[11] = 100;
    o = Options();
    o.local = LocalRule::StageTime;
    o.local_edits = true;
    o.candidate_budget = 8;
    o.edit_attempt_budget = 32;
    o.min_seed_service_ratio = .5;
    auto del = plan(deletion, topology(4, true), o);
    CHECK(validate_schedule(deletion, del).ok);
    CHECK(del.stages[0].flows.size() == 1);
    CHECK(del.stages[0].flows[0].bytes == 1);
    // Pressure prefers deleting the small edge: re-expansion must raise q to 100.
    o.global = GlobalRule::Pressure;
    del = plan(deletion, topology(4, true), o);
    CHECK(del.stages[0].flows.size() == 1);
    CHECK(del.stages[0].flows[0].bytes == 100);
    CHECK(del.stages[0].coefficient == 100);
    for (GlobalRule rule :
         {GlobalRule::MaxResidual, GlobalRule::SmoothResidual, GlobalRule::Pressure}) {
        o.global = rule;
        o.smooth_beta = rule == GlobalRule::SmoothResidual ? 4 : 0;
        del = plan(deletion, topology(4, true), o);
        CHECK(del.stages[0].coefficient == 100);
    }
    o.global = GlobalRule::None;
    o.smooth_beta = 0;
    for (LocalRule rule : {LocalRule::StageTime, LocalRule::SquaredWork, LocalRule::MaxPathTime}) {
        o.local = rule;
        del = plan(deletion, topology(4, true), o);
        CHECK(del.stages[0].flows.size() == (rule == LocalRule::MaxPathTime ? 2 : 1));
        CHECK(del.stages[0].flows[0].bytes == 1);
    }
    o.local = LocalRule::StageTime;
    o.min_seed_service_ratio = 1;
    del = plan(deletion, topology(4, true), o);
    CHECK(del.stages[0].flows.size() == 2);
    o.min_seed_service_ratio = .5;
    Demand singleton{2, {0, 1, 0, 0}};
    Plan one_edit = plan(singleton, topology(2), o);
    // Delete-last is invalid, as is insertion on the zero-demand free pair 1->0.
    CHECK(one_edit.edit_attempts == 2);
    CHECK(one_edit.candidate_evaluations == 1);
    o = Options();
    o.global = GlobalRule::Pressure;
    o.pressure_candidate = true;
    o.candidate_budget = 2;
    Plan pressure = plan(d, topology(4, true), o);
    CHECK(pressure.stages[0].coefficient == 19);
    const unsigned expected_dst[] = {3, 2, 1, 0};
    for (unsigned s = 0; s < 4; ++s)
        CHECK(pressure.stages[0].flows[s].dst_gpu == expected_dst[s]);
    // Even failed/duplicate edits consume the finite attempt budget.
    o = Options();
    o.global = GlobalRule::Pressure;
    o.local_edits = true;
    o.candidate_budget = 2;
    o.edit_attempt_budget = 1;
    o.min_seed_service_ratio = .5;
    del = plan(deletion, topology(4, true), o);
    CHECK(del.stages[0].coefficient == 100);
    CHECK(del.edit_attempts <= del.decomposition_steps);
    CHECK(del.candidate_evaluations <= 2 * del.decomposition_steps);
    // A heterogeneous fixed-route fixture exercises fixed delay weights, not homogeneous
    // cancellation.
    std::vector<native_topology::ResourceInfo> rs = {{"slow", .001}, {"fast", 1000000}};
    std::vector<native_topology::PathInfo> ps(16);
    for (unsigned s = 0; s < 4; ++s)
        for (unsigned dst = 0; dst < 4; ++dst)
            if (s != dst)
                ps[s * 4 + dst] =
                    native_topology::PathInfo({(s + dst) % 2}, s % 2 ? 1e9 : 1e-12, 0);
    auto heterogeneous = Topology::from_fixed_routes(4, 1, rs, ps);
    for (int sp = 0; sp < 3; ++sp)
        for (int global = 0; global < 4; ++global)
            for (int local = 0; local < 4; ++local) {
                o = Options();
                o.space = static_cast<Space>(sp);
                o.delay_alpha = sp == 2 ? 2 : 0;
                o.global = static_cast<GlobalRule>(global);
                o.local = static_cast<LocalRule>(local);
                if (global == 2)
                    o.smooth_beta = 4;
                if (global || local) {
                    o.pressure_candidate = true;
                    o.local_edits = true;
                    o.candidate_budget = 8;
                    o.edit_attempt_budget = 32;
                    o.min_seed_service_ratio = .5;
                }
                conserved(d, heterogeneous, o);
            }
}
void smooth_numeric_scales() {
    Demand demand{4, std::vector<uint64_t>(16, 0)};
    demand.bytes[1] = 1;
    demand.bytes[11] = 100;
    std::vector<native_topology::ResourceInfo> resources = {{"active", 100}, {"unused", 100}};
    std::vector<native_topology::PathInfo> paths(16);
    for (unsigned s = 0; s < 4; ++s)
        for (unsigned d = 0; d < 4; ++d)
            if (s != d)
                paths[s * 4 + d] = native_topology::PathInfo({0}, 5, 10);
    Options options;
    options.global = GlobalRule::SmoothResidual;
    options.local = LocalRule::StageTime;
    options.local_edits = true;
    options.candidate_budget = 8;
    options.edit_attempt_budget = 32;
    options.min_seed_service_ratio = .5;
    // For beta->0 the gradient is 1/2, so serving 100 bytes beats 1 or the seed's 2.
    // The same strict ordering holds for large beta; saturation must not erase the gain.
    for (double beta : {1e-20, 1e-300, std::numeric_limits<double>::denorm_min(), 4.0,
                        100.0, 1e300, std::numeric_limits<double>::max()}) {
        options.smooth_beta = beta;
        auto topology = Topology::from_fixed_routes(4, 1, resources, paths);
        Plan result = plan(demand, topology, options);
        CHECK(validate_schedule(demand, result).ok);
        CHECK(result.stages[0].flows.size() == 1);
        CHECK(result.stages[0].coefficient == 100);
        CHECK(result.stages[0].flows[0].src_gpu == 2);
        // At large beta, consuming the maximum can expose a different resource maximum.
        paths[1] = native_topology::PathInfo({1}, 5, 10);
        auto switched = Topology::from_fixed_routes(4, 1, resources, paths);
        Plan switched_result = plan(demand, switched, options);
        CHECK(validate_schedule(demand, switched_result).ok);
        CHECK(switched_result.stages[0].coefficient == 100);
        paths[1] = native_topology::PathInfo({0}, 5, 10);
    }
}
void review_characterization_tests() {
    Demand equal{4, std::vector<uint64_t>(16, 0)};
    equal.bytes[1] = equal.bytes[11] = 1000;
    auto independent = topology(4);
    Options options;
    options.local = LocalRule::SquaredWork;
    options.local_edits = true;
    options.candidate_budget = 8;
    options.edit_attempt_budget = 32;
    options.min_seed_service_ratio = .5;
    auto squared = plan(equal, independent, options);
    CHECK(squared.stages.size() == 2);
    CHECK(std::abs(evaluate_schedule(independent, squared).communication_us - 10.16) < 1e-12);

    // Strict global gain/time can improve this stage but worsen the complete schedule.
    Demand horizon = equal;
    horizon.bytes[1] = 1u << 20;
    horizon.bytes[11] = 100u << 20;
    auto horizon_topology = topology(4, false, 100, 5000);
    auto seed = plan(horizon, horizon_topology, Options());
    options.global = GlobalRule::Pressure;
    options.local = LocalRule::StageTime;
    auto greedy = plan(horizon, horizon_topology, options);
    CHECK(seed.stages[0].coefficient == (1u << 20));
    CHECK(greedy.stages[0].coefficient == (100u << 20));
    CHECK(std::abs(evaluate_schedule(horizon_topology, seed).communication_us - 18388.608) < 1e-9);
    CHECK(std::abs(evaluate_schedule(horizon_topology, greedy).communication_us - 18472.49408) < 1e-9);
    CHECK(8388.608 / 13388.608 > 84.7249408 / 5083.88608);

    std::vector<native_topology::ResourceInfo> resources = {{"fast", 1000}, {"slow", 1}};
    std::vector<native_topology::PathInfo> paths(16);
    for (unsigned s = 0; s < 4; ++s)
        for (unsigned d = 0; d < 4; ++d)
            if (s != d) paths[s * 4 + d] = native_topology::PathInfo({0}, 0, 0);
    paths[11] = native_topology::PathInfo({1}, 0, 0);
    auto mixed = Topology::from_fixed_routes(4, 1, resources, paths);
    auto bytes = plan(equal, mixed, Options());
    options = Options();options.space = Space::SerializationTime;
    auto time = plan(equal, mixed, options);
    CHECK(bytes.stages[0].coefficient == 1000);
    CHECK(std::abs(time.stages[0].coefficient - .008L) < 1e-15);
    for (unsigned f = 0; f < 2; ++f) {
        CHECK(bytes.stages[0].flows[f].src_gpu == time.stages[0].flows[f].src_gpu);
        CHECK(bytes.stages[0].flows[f].dst_gpu == time.stages[0].flows[f].dst_gpu);
    }
    CHECK(validate_schedule(equal, time).ok);
    resources[0].bandwidth_gbps = 1e300;resources[1].bandwidth_gbps = 1e-300;
    auto extreme = Topology::from_fixed_routes(4, 1, resources, paths);
    bool controlled = false;
    try {plan(equal, extreme, options);}
    catch(const std::invalid_argument& error) {
        controlled = std::string(error.what()) == "nonpositive or nonfinite output flow";
    }
    CHECK(controlled);
}
void cli() {
    char name[] = "/tmp/bvn-cli-XXXXXX";
    int fd = mkstemp(name);
    CHECK(fd >= 0);
    close(fd);
    std::string input = name, output = input + ".out";
    const std::string body = "BVN_CASE_V1 N 2 DEMAND 0 1024 2048 0 RESOURCES 2 0 forward 100 1 "
                             "reverse 50 PATHS 2 0 1 5 10 1 0 1 0 7 14 1 1";
    {
        std::ofstream f(input);
        f << body;
    }
    auto run = [&](std::string arguments) {
        return std::system(
            ("./build/bvn_simulator " + arguments + " >" + output + " 2>/dev/null").c_str());
    };
    CHECK(run("--help") == 0);
    for (const std::string method :
         {"fast-native", "partial-endpoint", "partial-topology", "partial-time", "partial-delay"}) {
        CHECK(run("--input " + input + " --method " + method + " --repeat 3") == 0);
        std::string command =
            "python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); assert "
            "x[\"schema_version\"]==1; assert len(x[\"planning_samples_us\"])==3; assert "
            "x[\"demand_bytes\"]==3072; assert sum(f[\"bytes\"] for s in x[\"stages\"] for f in "
            "s[\"flows\"])==3072; assert len(x[\"options\"])==12; assert "
            "x[\"max_pair_error_bytes\"]<=1e-6' " +
            output;
        CHECK(std::system(command.c_str()) == 0);
    }
    CHECK(run("--input " + input + " --method partial-topology --global smooth") == 0);
    CHECK(std::system(("python3 -c 'import json,sys; assert "
                       "json.load(open(sys.argv[1]))[\"options\"][\"smooth_beta\"]==4' " +
                       output)
                          .c_str()) == 0);
    for (const std::string bad :
         {"--unknown 1", "--repeat 2 --repeat 3", "--repeat 101", "--repeat -1", "--rho nan",
          "--rho inf", "--candidate-budget 1.5", "--edits 2", "--space nonsense",
          "--global smooth --beta 0", "--unit-bytes 3", "--repeat"})
        CHECK(run("--input " + input + " --method partial-endpoint " + bad) != 0);
    for (const std::string &corrupt :
         {body + " extra", std::string("BAD") + body, body.substr(0, body.size() - 1),
          std::string("BVN_CASE_V1 N 1 DEMAND -1 RESOURCES 0 PATHS 0"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 0 a 100 PATHS 2 0 1 5 10 1 0 0 1 "
                      "5 10 1 0"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 0 a 100 PATHS 1 0 1 5 10 1 0"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 2 0 a 100 1 a 100 PATHS 2"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 1 a 100 PATHS 2"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 0 a 0 PATHS 2"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 0 a 100 PATHS 2 0 0 0 0 1 0"),
          std::string(
              "BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 2 0 a 100 1 b 100 PATHS 2 0 1 0 0 2 0 0"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 0 a 100 PATHS 2 0 1 -1 0 1 0"),
          std::string("BVN_CASE_V1 N 2 DEMAND 0 1 0 0 RESOURCES 1 0 a? 100 PATHS 2")}) {
        {
            std::ofstream f(input);
            f << corrupt;
        }
        CHECK(run("--input " + input + " --method partial-endpoint") != 0);
    }
    std::remove(input.c_str());
    std::remove(output.c_str());
}
int main(int argc, char **) {
    try {
        noop_configuration_test();
        basic();
        invalid();
        objectives();
        smooth_numeric_scales();
        review_characterization_tests();
        if (argc > 1)
            cli();
        std::cout << "bvn schedule: " << assertions << " checks passed\n";
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
