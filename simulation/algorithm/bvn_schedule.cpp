#include "bvn_schedule.h"
#include "all2all.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <set>
#include <stdexcept>

namespace bvn {
namespace {
const long double relative_tolerance = 64 * std::numeric_limits<double>::epsilon();
long double tolerance(long double x) {
    return std::max(1e-6L, relative_tolerance * std::abs(x));
}
void require(bool v, const char *message) {
    if (!v)
        throw std::invalid_argument(message);
}
void finite(long double x, const char *message) {
    if (!std::isfinite(x))
        throw std::overflow_error(message);
}
void demand_structure(const Demand &d) {
    require(d.n >= 1 && d.n <= 64, "N must be in [1,64]");
    require(d.bytes.size() == size_t(d.n) * d.n, "demand must contain N squared entries");
    uint64_t total = 0;
    for (unsigned s = 0; s < d.n; ++s)
        for (unsigned dst = 0; dst < d.n; ++dst) {
            uint64_t x = d.bytes[s * d.n + dst];
            require(s != dst || x == 0, "nonzero diagonal demand");
            if (x > (uint64_t(1) << 53) - total)
                throw std::overflow_error("total demand exceeds 2^53 bytes");
            total += x;
        }
}
void topology_structure(const Topology &t, unsigned n) {
    require(t.n == n && t.g == 1, "topology must have N endpoints and g=1");
    for (unsigned e = 0; e < t.resource_count(); ++e)
        require(std::isfinite(t.resource(e).bandwidth_gbps) && t.resource(e).bandwidth_gbps > 0,
                "invalid resource bandwidth");
    for (unsigned s = 0; s < n; ++s)
        for (unsigned d = 0; d < n; ++d) {
            const auto &p = t.gpu_path(s, d);
            require(std::isfinite(p.propagation_us) && p.propagation_us >= 0 &&
                        std::isfinite(p.rtt_us) && p.rtt_us >= 0,
                    "invalid path delay");
            require(s == d || !p.resource_ids.empty(), "missing non-self path");
            std::set<unsigned> seen;
            for (unsigned e : p.resource_ids)
                require(e < t.resource_count() && seen.insert(e).second,
                        "invalid or repeated path resource");
        }
}
void stage_structure(const Stage &s, unsigned n, Space space) {
    require(!s.flows.empty(), "empty execution stage");
    require(std::isfinite(s.coefficient) && s.coefficient > 0, "invalid stage coefficient");
    if (space == Space::Bytes)
        require(std::floor(s.coefficient) == s.coefficient, "noninteger Bytes coefficient");
    std::vector<bool> src(n, false), dst(n, false);
    for (const auto &f : s.flows) {
        require(f.src_gpu < n && f.dst_gpu < n && f.src_gpu != f.dst_gpu, "invalid flow endpoint");
        require(!src[f.src_gpu] && !dst[f.dst_gpu], "duplicate stage source or destination");
        src[f.src_gpu] = dst[f.dst_gpu] = true;
        require(std::isfinite(f.bytes) && f.bytes > 0, "invalid flow bytes");
        if (space == Space::Bytes)
            require(std::floor(f.bytes) == f.bytes, "noninteger Bytes flow");
    }
}
void plan_enums(const Plan &p) {
    require(p.engine == Engine::Partial || p.engine == Engine::FastNative,
            "unsupported plan engine");
    require(p.space == Space::Bytes || p.space == Space::SerializationTime ||
                p.space == Space::DelayAdjustedTime,
            "unsupported plan space");
    require(p.engine != Engine::FastNative || p.space == Space::Bytes,
            "native plan must use bytes");
}

// Native FAST adapter: preserve its order/frequencies, project out virtual work.
Plan plan_native(const Demand &d, const Options &o) {
    Plan out;
    out.engine = Engine::FastNative;
    if (std::none_of(d.bytes.begin(), d.bytes.end(), [](uint64_t x) { return x != 0; }))
        return out;
    std::vector<unsigned> units;
    for (uint64_t x : d.bytes)
        units.push_back(unsigned(x / o.native_unit_bytes));
    Matrix m(units.data(), d.n);
    FastAll2All fast(&m, 1, INFB, DGX2, double(o.native_unit_bytes) / 1e6);
    fast.to_scaled_doubly_stochastic_matrix();
    fast.decompose();
    std::vector<uint64_t> remaining = d.bytes;
    for (const auto &p : fast.p_sets) {
        ++out.decomposition_steps;
        if (p.get_freq() > std::numeric_limits<uint64_t>::max() / o.native_unit_bytes)
            throw std::overflow_error("native frequency bytes overflow");
        uint64_t q = uint64_t(p.get_freq()) * o.native_unit_bytes;
        Stage s;
        s.coefficient = q;
        for (const auto &edge : p.mp) {
            require(edge.first < d.n && edge.second < d.n, "invalid native permutation");
            auto &r = remaining[edge.first * d.n + edge.second];
            uint64_t x = std::min(r, q);
            r -= x;
            if (x && edge.first != edge.second)
                s.flows.push_back({edge.first, edge.second, double(x)});
        }
        if (!s.flows.empty())
            out.stages.push_back(std::move(s));
    }
    require(std::none_of(remaining.begin(), remaining.end(), [](uint64_t x) { return x != 0; }),
            "native adapter left real demand");
    return out;
}

// Partial decomposition. All trial candidates read the same real residual.
struct Workspace {
    unsigned n;
    std::vector<long double> remaining, weight, residual, work, delivered;
    std::vector<long double> priority, row_priority, after;
    std::vector<unsigned> rows;
    std::vector<std::vector<unsigned>> adjacency;
    std::vector<int> inverse;
    std::vector<unsigned char> visited;
    long double reference = 0;
};
struct Candidate {
    std::vector<int> dst;
    Stage stage;
    StageCost cost;
    std::vector<long double> work;
    long double rate = 0, local = 0, real_bytes = 0;
};
Workspace prepare(const Demand &d, const Topology &t, const Options &o) {
    // 权重只由初始需求确定；传播项不能在每轮残余上重复加入。
    Workspace w;
    w.n = d.n;
    w.remaining.assign(d.bytes.begin(), d.bytes.end());
    w.weight.assign(d.bytes.size(), 0);
    w.residual.resize(d.bytes.size());
    w.delivered.assign(d.bytes.size(), 0);
    if (o.global != GlobalRule::None || o.local != LocalRule::None)
        w.work.assign(t.resource_count(), 0);
    w.after.resize(w.work.size());
    w.rows.resize(d.n);
    w.adjacency.resize(d.n);
    w.inverse.resize(d.n);
    w.visited.resize(d.n);
    for (auto &neighbors : w.adjacency)
        neighbors.reserve(d.n);
    if (o.pressure_candidate) {
        w.priority.resize(d.bytes.size());
        w.row_priority.resize(d.n);
    }
    for (unsigned s = 0; s < d.n; ++s)
        for (unsigned dst = 0; dst < d.n; ++dst) {
            size_t k = s * d.n + dst;
            if (!d.bytes[k])
                continue;
            long double weight = 1;
            if (o.space != Space::Bytes) {
                weight = .008L / t.path_min_bandwidth_gbps(s, dst);
                if (o.space == Space::DelayAdjustedTime)
                    weight += static_cast<long double>(o.delay_alpha) *
                              t.gpu_path(s, dst).propagation_us / d.bytes[k];
            }
            require(std::isfinite(weight) && weight > 0, "nonpositive or nonfinite mapping weight");
            w.weight[k] = weight;
            w.residual[k] = weight * w.remaining[k];
            finite(w.residual[k], "auxiliary residual overflow");
            if (!w.work.empty())
                for (unsigned e : t.gpu_path(s, dst).resource_ids)
                    w.work[e] += .008L * w.remaining[k] / t.resource(e).bandwidth_gbps;
        }
    for (long double h : w.work) {
        finite(h, "resource work overflow");
        w.reference = std::max(w.reference, h);
    }
    return w;
}
std::vector<int> matching(Workspace &w, const Topology &t, bool pressure) {
    // 同一Kuhn增广过程只改变遍历顺序，不声称求解最大权匹配。
    unsigned n = w.n;
    auto &rows = w.rows;
    auto &adj = w.adjacency;
    auto &priority = w.priority;
    auto &max_priority = w.row_priority;
    if (pressure) {
        std::fill(priority.begin(), priority.end(), 0);
        std::fill(max_priority.begin(), max_priority.end(), 0);
    }
    for (unsigned s = 0; s < n; ++s) {
        rows[s] = s;
        adj[s].clear();
        for (unsigned d = 0; d < n; ++d)
            if (w.remaining[s * n + d] > 0) {
                adj[s].push_back(d);
                if (pressure) {
                    for (unsigned e : t.gpu_path(s, d).resource_ids)
                        priority[s * n + d] += (w.work[e] / w.reference) * .008L *
                                               w.remaining[s * n + d] /
                                               t.resource(e).bandwidth_gbps;
                    max_priority[s] = std::max(max_priority[s], priority[s * n + d]);
                }
            }
    }
    if (pressure) {
        std::sort(rows.begin(), rows.end(), [&](unsigned a, unsigned b) {
            return max_priority[a] != max_priority[b] ? max_priority[a] > max_priority[b] : a < b;
        });
        for (unsigned s = 0; s < n; ++s)
            std::sort(adj[s].begin(), adj[s].end(), [&](unsigned a, unsigned b) {
                return priority[s * n + a] != priority[s * n + b]
                           ? priority[s * n + a] > priority[s * n + b]
                           : a < b;
            });
    }
    auto &inverse = w.inverse;
    auto &seen = w.visited;
    std::fill(inverse.begin(), inverse.end(), -1);
    std::vector<int> dst(n, -1);
    std::function<bool(unsigned)> augment = [&](unsigned s) {
        for (unsigned d : adj[s])
            if (!seen[d]) {
                seen[d] = 1;
                if (inverse[d] < 0 || augment(unsigned(inverse[d]))) {
                    inverse[d] = s;
                    return true;
                }
            }
        return false;
    };
    for (unsigned s : rows) {
        std::fill(seen.begin(), seen.end(), 0);
        augment(s);
    }
    for (unsigned d = 0; d < n; ++d)
        if (inverse[d] >= 0)
            dst[inverse[d]] = d;
    return dst;
}
long double smooth_gain(const std::vector<long double> &work,
                        const std::vector<long double> &consumed, long double reference,
                        double beta) {
    const long double b = beta;
    const long double maximum = *std::max_element(work.begin(), work.end());
    long double weight_sum = 0, weighted_change = 0;
    for (size_t e = 0; e < work.size(); ++e) {
        const long double weight = std::exp(b * ((work[e] - maximum) / reference));
        // 直接使用真实候选g；不能用H-H'重建微小减量。min仅对应舍入超扣时的H'=0。
        const long double g = std::min(consumed[e], work[e]);
        weight_sum += weight;
        weighted_change += weight * std::expm1(-b * (g / reference));
    }
    const long double mean_change = weighted_change / weight_sum;
    // 小beta下直接计算势函数之差，避免两个含log(resource_count)/beta的大数相减。
    if (mean_change > -.5L)
        return -(reference / b) * std::log1p(mean_change);

    // 大beta下expm1可能饱和为-1。分离最大值下降与两个居中logsumexp项；
    // 用min(maxH-H+g)求最大值下降，不再相减两个几乎相等的绝对最大值。
    long double maximum_drop = std::numeric_limits<long double>::infinity();
    for (size_t e = 0; e < work.size(); ++e)
        maximum_drop = std::min(maximum_drop,
                                (maximum - work[e]) + std::min(consumed[e], work[e]));
    long double after_weight_sum = 0;
    for (size_t e = 0; e < work.size(); ++e) {
        const long double gap = (maximum - work[e]) + std::min(consumed[e], work[e]);
        after_weight_sum += std::exp(b * ((maximum_drop - gap) / reference));
    }
    return maximum_drop + (reference / b) *
                              (std::log(weight_sum) - std::log(after_weight_sum));
}
Candidate materialize(const std::vector<int> &dst, Workspace &w, const Topology &t,
                      const Options &o) {
    // 每个候选重新计算公共q并恢复真实字节，评分绝不直接比较不同空间的q。
    Candidate c;
    c.dst = dst;
    c.stage.coefficient = std::numeric_limits<long double>::infinity();
    for (unsigned s = 0; s < w.n; ++s)
        if (dst[s] >= 0)
            c.stage.coefficient = std::min(c.stage.coefficient, w.residual[s * w.n + dst[s]]);
    require(std::isfinite(c.stage.coefficient) && c.stage.coefficient > 0,
            "candidate has no positive coefficient");
    for (unsigned s = 0; s < w.n; ++s)
        if (dst[s] >= 0) {
            size_t k = s * w.n + dst[s];
            bool limiting = w.residual[k] == c.stage.coefficient;
            long double actual = limiting ? w.remaining[k] : c.stage.coefficient / w.weight[k];
            double x = double(actual);
            if (!limiting && static_cast<long double>(x) > w.remaining[k])
                x = std::nextafter(x, 0.0);
            require(std::isfinite(x) && x > 0, "nonpositive or nonfinite output flow");
            require(std::abs(static_cast<long double>(x) - actual) <= tolerance(w.remaining[k]),
                    "output flow rounding exceeds tolerance");
            c.stage.flows.push_back({s, unsigned(dst[s]), x});
            c.real_bytes += x;
        }
    stage_structure(c.stage, w.n, o.space);
    if (o.global == GlobalRule::None && o.local == LocalRule::None)
        return c;
    c.cost = t.evaluate_gpu(c.stage.flows);
    require(std::isfinite(c.cost.total_us) && c.cost.total_us > 0,
            "candidate time must be finite and positive");
    c.work.resize(w.work.size());
    auto &after = w.after;
    long double before_max = 0, after_max = 0;
    for (unsigned e = 0; e < w.work.size(); ++e) {
        c.work[e] = .008L * c.cost.resource_bytes[e] / t.resource(e).bandwidth_gbps;
        after[e] = w.work[e] - c.work[e];
        require(after[e] >= -relative_tolerance * std::max(1.L, w.reference),
                "candidate resource work overdraw");
        after[e] = std::max(0.L, after[e]);
        before_max = std::max(before_max, w.work[e]);
        after_max = std::max(after_max, after[e]);
    }
    // 全局收益按物理阶段时间归一化；局部目标仅在字典序第二层比较。
    if (o.global == GlobalRule::MaxResidual)
        c.rate = (before_max - after_max) / c.cost.total_us;
    else if (o.global == GlobalRule::SmoothResidual)
        c.rate = smooth_gain(w.work, c.work, w.reference, o.smooth_beta) / c.cost.total_us;
    else if (o.global == GlobalRule::Pressure) {
        for (unsigned e = 0; e < w.work.size(); ++e)
            c.rate += (w.work[e] / w.reference) * c.work[e];
        c.rate /= c.cost.total_us;
    }
    if (o.local == LocalRule::StageTime)
        c.local = c.cost.total_us;
    else if (o.local == LocalRule::SquaredWork)
        for (long double x : c.work)
            c.local += x * x;
    else if (o.local == LocalRule::MaxPathTime)
        for (const auto &f : c.stage.flows)
            c.local = std::max(
                c.local, static_cast<long double>(t.gpu_path(f.src_gpu, f.dst_gpu).propagation_us) +
                             .008L * f.bytes / t.path_min_bandwidth_gbps(f.src_gpu, f.dst_gpu));
    finite(c.rate, "global score overflow");
    finite(c.local, "local score overflow");
    return c;
}
int compare(long double a, long double b) {
    long double tol = relative_tolerance * std::max(1.L, std::max(std::abs(a), std::abs(b)));
    return a > b + tol ? 1 : (b > a + tol ? -1 : 0);
}
bool better(const Candidate &a, const Candidate &b, const Options &o) {
    int g = o.global == GlobalRule::None ? 0 : compare(a.rate, b.rate);
    if (g)
        return g > 0;
    int l = o.local == LocalRule::None ? 0 : compare(a.local, b.local);
    return l ? l < 0 : a.dst < b.dst;
}
bool legal(const std::vector<int> &dst, const Workspace &w) {
    std::vector<bool> used(w.n, false);
    bool any = false;
    for (unsigned s = 0; s < w.n; ++s)
        if (dst[s] >= 0) {
            unsigned d = dst[s];
            if (d >= w.n || used[d] || w.remaining[s * w.n + d] <= 0)
                return false;
            used[d] = true;
            any = true;
        }
    return any;
}
// 唯一消费入口：按输出double流量扣减，限制边直接耗尽并显式检查舍入误差。
void consume(Workspace &w, const Candidate &c) {
    unsigned exhausted = 0;
    for (const auto &f : c.stage.flows) {
        size_t k = f.src_gpu * w.n + f.dst_gpu;
        bool limiting = w.residual[k] == c.stage.coefficient;
        long double next = w.remaining[k] - f.bytes;
        w.delivered[k] += f.bytes;
        if (limiting) {
            require(std::abs(next) <= tolerance(w.remaining[k]),
                    "limiting edge rounding exceeds tolerance");
            next = 0;
            ++exhausted;
        } else
            require(next >= 0, "flow overdraw");
        w.remaining[k] = next;
        w.residual[k] = w.weight[k] * next;
    }
    require(exhausted > 0, "candidate made no support progress");
    for (unsigned e = 0; e < w.work.size(); ++e) {
        w.work[e] -= c.work[e];
        require(w.work[e] >= -relative_tolerance * std::max(1.L, w.reference),
                "negative residual resource work");
        w.work[e] = std::max(0.L, w.work[e]);
    }
}
Plan plan_partial(const Demand &d, const Topology &t, const Options &o) {
    Plan out;
    out.space = o.space;
    Workspace w = prepare(d, t, o);
    uint64_t support =
        std::count_if(d.bytes.begin(), d.bytes.end(), [](uint64_t x) { return x > 0; });
    while (
        std::any_of(w.remaining.begin(), w.remaining.end(), [](long double x) { return x > 0; })) {
        require(out.decomposition_steps < support,
                "partial decomposition exceeded initial support bound");
        Candidate best = materialize(matching(w, t, false), w, t, o);
        long double seed_bytes = best.real_bytes;
        unsigned evaluations = 1, attempts = 0;
        std::set<std::vector<int>> seen;
        seen.insert(best.dst);
        auto consider = [&](const std::vector<int> &dst) {
            if (evaluations >= o.candidate_budget || !legal(dst, w) || !seen.insert(dst).second)
                return false;
            Candidate c = materialize(dst, w, t, o);
            ++evaluations;
            if (c.real_bytes < o.min_seed_service_ratio * seed_bytes)
                return false;
            if (better(c, best, o)) {
                best = std::move(c);
                return true;
            }
            return false;
        };
        if (o.pressure_candidate && evaluations < o.candidate_budget)
            consider(matching(w, t, true));
        // 删除、插入、二边交换依次扫描；改进后重启，但预算与去重集合不重置。
        if (o.local_edits) {
            bool improved = true;
            while (improved && evaluations < o.candidate_budget &&
                   attempts < o.edit_attempt_budget) {
                improved = false;
                const std::vector<int> base = best.dst;
                auto trial = [&](const std::vector<int> &next) {
                    if (attempts >= o.edit_attempt_budget || evaluations >= o.candidate_budget)
                        return false;
                    ++attempts;
                    return consider(next);
                };
                for (unsigned s = 0; s < d.n && !improved && attempts < o.edit_attempt_budget &&
                                     evaluations < o.candidate_budget;
                     ++s)
                    if (base[s] >= 0) {
                        auto v = base;
                        v[s] = -1;
                        improved = trial(v);
                    }
                for (unsigned s = 0; s < d.n && !improved && attempts < o.edit_attempt_budget &&
                                     evaluations < o.candidate_budget;
                     ++s)
                    if (base[s] < 0)
                        for (unsigned dst = 0;
                             dst < d.n && !improved && attempts < o.edit_attempt_budget &&
                             evaluations < o.candidate_budget;
                             ++dst) {
                            if (std::find(base.begin(), base.end(), int(dst)) != base.end())
                                continue;
                            auto v = base;
                            v[s] = dst;
                            improved = trial(v);
                        }
                for (unsigned s = 0; s < d.n && !improved && attempts < o.edit_attempt_budget &&
                                     evaluations < o.candidate_budget;
                     ++s)
                    if (base[s] >= 0)
                        for (unsigned other = s + 1;
                             other < d.n && !improved && attempts < o.edit_attempt_budget &&
                             evaluations < o.candidate_budget;
                             ++other)
                            if (base[other] >= 0) {
                                auto v = base;
                                std::swap(v[s], v[other]);
                                improved = trial(v);
                            }
            }
        }
        consume(w, best);
        out.stages.push_back(std::move(best.stage));
        ++out.decomposition_steps;
        out.candidate_evaluations += evaluations;
        out.edit_attempts += attempts;
    }
    for (size_t k = 0; k < d.bytes.size(); ++k)
        require(std::abs(w.delivered[k] - d.bytes[k]) <=
                    (o.space == Space::Bytes ? 0 : tolerance(d.bytes[k])),
                "output rounding exceeded pair tolerance");
    return out;
}
} // namespace

void validate_options(const Demand &d, const Topology &t, const Options &o) {
    demand_structure(d);
    topology_structure(t, d.n);
    require(o.engine == Engine::FastNative || o.engine == Engine::Partial, "unsupported engine");
    require(o.space == Space::Bytes || o.space == Space::SerializationTime ||
                o.space == Space::DelayAdjustedTime,
            "unsupported space");
    require(o.global == GlobalRule::None || o.global == GlobalRule::MaxResidual ||
                o.global == GlobalRule::SmoothResidual || o.global == GlobalRule::Pressure,
            "unsupported global rule");
    require(o.local == LocalRule::None || o.local == LocalRule::StageTime ||
                o.local == LocalRule::SquaredWork || o.local == LocalRule::MaxPathTime,
            "unsupported local rule");
    require(o.candidate_budget > 0, "candidate budget must be positive");
    require(std::isfinite(o.min_seed_service_ratio) && o.min_seed_service_ratio > 0 &&
                o.min_seed_service_ratio <= 1,
            "rho must be in (0,1]");
    require(std::isfinite(o.smooth_beta) &&
                (o.global == GlobalRule::SmoothResidual ? o.smooth_beta > 0 : o.smooth_beta == 0),
            "beta is positive only for smooth objective");
    require(std::isfinite(o.delay_alpha) && o.delay_alpha >= 0 &&
                (o.space == Space::DelayAdjustedTime || o.delay_alpha == 0),
            "alpha is nonnegative and delay-only");
    require(o.local_edits ? (o.edit_attempt_budget > 0 && o.candidate_budget >= 2)
                          : o.edit_attempt_budget == 0,
            "incompatible edit budget");
    require(!o.pressure_candidate || o.candidate_budget >= 2,
            "pressure requires at least two candidates");
    if (o.global == GlobalRule::None && o.local == LocalRule::None)
        require(!o.pressure_candidate && !o.local_edits && o.candidate_budget == 1,
                "endpoint baseline accepts only seed");
    require(o.native_unit_bytes > 0, "native unit must be positive");
    if (o.engine == Engine::FastNative) {
        require(o.space == Space::Bytes && o.global == GlobalRule::None &&
                    o.local == LocalRule::None && !o.pressure_candidate && !o.local_edits &&
                    o.candidate_budget == 1 && o.edit_attempt_budget == 0 &&
                    o.min_seed_service_ratio == 1 && o.smooth_beta == 0 && o.delay_alpha == 0,
                "native accepts only baseline settings");
        std::vector<uint64_t> rows(d.n, 0), cols(d.n, 0);
        for (unsigned s = 0; s < d.n; ++s)
            for (unsigned dst = 0; dst < d.n; ++dst) {
                uint64_t x = d.bytes[s * d.n + dst];
                require(x % o.native_unit_bytes == 0, "native unit does not exactly divide demand");
                x /= o.native_unit_bytes;
                if (x > INT32_MAX)
                    throw std::overflow_error("native cell exceeds INT32_MAX");
                rows[s] += x;
                cols[dst] += x;
            }
        for (unsigned i = 0; i < d.n; ++i)
            if (rows[i] > UINT32_MAX || cols[i] > UINT32_MAX)
                throw std::overflow_error("native row or column exceeds UINT32_MAX");
    } else
        require(o.native_unit_bytes == 1, "partial requires unit_bytes=1");
}
Plan plan(const Demand &d, const Topology &t, const Options &o) {
    validate_options(d, t, o);
    return o.engine == Engine::FastNative ? plan_native(d, o) : plan_partial(d, t, o);
}
Validation validate_schedule(const Demand &d, const Plan &p) {
    Validation result;
    try {
        demand_structure(d);
        plan_enums(p);
        std::vector<long double> sent(d.bytes.size(), 0);
        for (const auto &s : p.stages) {
            stage_structure(s, d.n, p.space);
            for (const auto &f : s.flows) {
                size_t k = f.src_gpu * d.n + f.dst_gpu;
                require(d.bytes[k] > 0, "flow on zero-demand pair");
                sent[k] += f.bytes;
                long double excess = sent[k] - d.bytes[k];
                if (excess > (p.space == Space::Bytes ? 0 : tolerance(d.bytes[k])))
                    throw std::invalid_argument("pair demand overdraw");
            }
        }
        for (size_t k = 0; k < sent.size(); ++k) {
            long double error = std::abs(sent[k] - d.bytes[k]);
            result.max_pair_error_bytes = std::max(result.max_pair_error_bytes, error);
            if (error > (p.space == Space::Bytes ? 0 : tolerance(d.bytes[k])))
                throw std::invalid_argument("pair demand conservation failure");
        }
        result.ok = true;
        result.message = "ok";
    } catch (const std::exception &e) {
        result.message = e.what();
    }
    return result;
}
Evaluation evaluate_schedule(const Topology &t, const Plan &p) {
    plan_enums(p);
    topology_structure(t, t.n);
    Evaluation out;
    out.resource_total_bytes.assign(t.resource_count(), 0);
    for (const auto &s : p.stages) {
        stage_structure(s, t.n, p.space);
        StageCost cost = t.evaluate_gpu(s.flows);
        out.communication_us += cost.total_us;
        out.serialization_sum_us += cost.serial_us;
        out.propagation_sum_us += cost.alpha_us;
        for (unsigned e = 0; e < t.resource_count(); ++e) {
            out.resource_total_bytes[e] += cost.resource_bytes[e];
            finite(out.resource_total_bytes[e], "total resource bytes overflow");
        }
        out.stages.push_back(std::move(cost));
    }
    finite(out.communication_us, "total communication time overflow");
    finite(out.serialization_sum_us, "total serialization overflow");
    finite(out.propagation_sum_us, "total propagation overflow");
    return out;
}
} // namespace bvn
