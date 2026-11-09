#include "algorithm/bvn_schedule.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>

#ifndef BVN_GIT_REVISION
#define BVN_GIT_REVISION "unknown"
#endif
#ifndef BVN_GIT_DIRTY
#define BVN_GIT_DIRTY "unknown"
#endif

namespace {
using namespace bvn;
using Clock = std::chrono::steady_clock;
double elapsed(Clock::time_point start) {
    return std::chrono::duration<double, std::micro>(Clock::now() - start).count();
}
void need(bool condition, const std::string &message) {
    if (!condition)
        throw std::invalid_argument(message);
}
uint64_t integer(const std::string &value) {
    need(!value.empty(), "empty integer");
    uint64_t result = 0;
    for (char c : value) {
        need(c >= '0' && c <= '9', "invalid unsigned integer: " + value);
        unsigned digit = c - '0';
        if (result > (UINT64_MAX - digit) / 10)
            throw std::overflow_error("integer overflow: " + value);
        result = result * 10 + digit;
    }
    return result;
}
unsigned bounded(const std::string &value, uint64_t maximum) {
    uint64_t x = integer(value);
    need(x <= maximum, "integer out of range: " + value);
    return unsigned(x);
}
double number(const std::string &value) {
    static const std::regex syntax("[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?");
    need(std::regex_match(value, syntax), "invalid number: " + value);
    size_t consumed = 0;
    double x = std::stod(value, &consumed);
    need(consumed == value.size() && std::isfinite(x), "nonfinite number: " + value);
    return x;
}
std::string token(std::istream &input) {
    std::string result;
    need(bool(input >> result), "unexpected end of input");
    return result;
}
void label(std::istream &input, const char *expected) {
    need(token(input) == expected, std::string("expected ") + expected);
}
std::string quoted(const std::string &text) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : text) {
        if (c == '"' || c == '\\')
            out << '\\' << c;
        else if (c < 32)
            out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c)
                << std::dec;
        else
            out << c;
    }
    out << '"';
    return out.str();
}
const char *engine_name(Engine e) {
    return e == Engine::FastNative ? "fast-native" : "partial";
}
const char *space_name(Space s) {
    return s == Space::Bytes ? "bytes" : s == Space::SerializationTime ? "time" : "delay";
}
const char *global_name(GlobalRule g) {
    switch (g) {
    case GlobalRule::None:
        return "none";
    case GlobalRule::MaxResidual:
        return "max";
    case GlobalRule::SmoothResidual:
        return "smooth";
    case GlobalRule::Pressure:
        return "pressure";
    }
    throw std::invalid_argument("unsupported global rule");
}
const char *local_name(LocalRule l) {
    switch (l) {
    case LocalRule::None:
        return "none";
    case LocalRule::StageTime:
        return "stage";
    case LocalRule::SquaredWork:
        return "square";
    case LocalRule::MaxPathTime:
        return "path";
    }
    throw std::invalid_argument("unsupported local rule");
}
Options preset(const std::string &method) {
    Options o;
    if (method == "fast-native")
        o.engine = Engine::FastNative;
    else if (method == "partial-topology") {
        o.global = GlobalRule::Pressure;
        o.local = LocalRule::StageTime;
        o.pressure_candidate = o.local_edits = true;
        o.candidate_budget = 8;
        o.edit_attempt_budget = 32;
        o.min_seed_service_ratio = .5;
    } else if (method == "partial-time")
        o.space = Space::SerializationTime;
    else if (method == "partial-delay") {
        o.space = Space::DelayAdjustedTime;
        o.delay_alpha = 1;
    } else
        need(method == "partial-endpoint", "unknown method: " + method);
    return o;
}
std::map<std::string, std::string> arguments(int argc, char **argv) {
    const std::set<std::string> allowed = {"--input",       "--method",    "--repeat",
                                           "--space",       "--global",    "--local",
                                           "--pressure",    "--edits",     "--candidate-budget",
                                           "--edit-budget", "--rho",       "--beta",
                                           "--delay-alpha", "--unit-bytes"};
    std::map<std::string, std::string> values;
    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i];
        need(allowed.count(key), "unknown parameter: " + key);
        need(!values.count(key), "duplicate parameter: " + key);
        need(i + 1 < argc && std::string(argv[i + 1]).compare(0, 2, "--") != 0,
             "missing value: " + key);
        values[key] = argv[++i];
    }
    need(values.count("--input") && values.count("--method"), "--input and --method are required");
    return values;
}
Options overrides(Options o, const std::map<std::string, std::string> &args) {
    for (const auto &a : args) {
        const auto &k = a.first;
        const auto &v = a.second;
        if (k == "--space") {
            need(v == "bytes" || v == "time" || v == "delay", "invalid space");
            o.space = v == "bytes"  ? Space::Bytes
                      : v == "time" ? Space::SerializationTime
                                    : Space::DelayAdjustedTime;
        } else if (k == "--global") {
            need(v == "none" || v == "max" || v == "smooth" || v == "pressure",
                 "invalid global rule");
            o.global = v == "none"     ? GlobalRule::None
                       : v == "max"    ? GlobalRule::MaxResidual
                       : v == "smooth" ? GlobalRule::SmoothResidual
                                       : GlobalRule::Pressure;
        } else if (k == "--local") {
            need(v == "none" || v == "stage" || v == "square" || v == "path", "invalid local rule");
            o.local = v == "none"     ? LocalRule::None
                      : v == "stage"  ? LocalRule::StageTime
                      : v == "square" ? LocalRule::SquaredWork
                                      : LocalRule::MaxPathTime;
        } else if (k == "--pressure")
            o.pressure_candidate = bounded(v, 1);
        else if (k == "--edits")
            o.local_edits = bounded(v, 1);
        else if (k == "--candidate-budget")
            o.candidate_budget = bounded(v, UINT32_MAX);
        else if (k == "--edit-budget")
            o.edit_attempt_budget = bounded(v, UINT32_MAX);
        else if (k == "--rho")
            o.min_seed_service_ratio = number(v);
        else if (k == "--beta")
            o.smooth_beta = number(v);
        else if (k == "--delay-alpha")
            o.delay_alpha = number(v);
        else if (k == "--unit-bytes")
            o.native_unit_bytes = integer(v);
    }
    if (o.global == GlobalRule::SmoothResidual && !args.count("--beta"))
        o.smooth_beta = 4;
    return o;
}
Topology read_case(const std::string &name, Demand &demand, double &setup_us) {
    std::ifstream input(name);
    need(bool(input), "cannot open input: " + name);
    label(input, "BVN_CASE_V1");
    label(input, "N");
    demand.n = bounded(token(input), 64);
    need(demand.n >= 1, "N must be positive");
    label(input, "DEMAND");
    demand.bytes.resize(demand.n * demand.n);
    for (auto &x : demand.bytes)
        x = integer(token(input));
    label(input, "RESOURCES");
    unsigned count = bounded(token(input), UINT32_MAX);
    std::vector<native_topology::ResourceInfo> resources;
    std::set<std::string> names;
    const std::regex name_syntax("[A-Za-z0-9_.:-]+");
    // Incremental parsing rejects truncated huge counts before allocating by count.
    for (unsigned e = 0; e < count; ++e) {
        need(integer(token(input)) == e, "resource IDs must be consecutive");
        std::string resource_name = token(input);
        need(std::regex_match(resource_name, name_syntax) && names.insert(resource_name).second,
             "invalid or duplicate resource name");
        double bandwidth = number(token(input));
        need(bandwidth > 0, "resource bandwidth must be positive");
        resources.push_back({resource_name, bandwidth});
    }
    label(input, "PATHS");
    need(integer(token(input)) == demand.n * (demand.n - 1),
         "PATHS must contain every non-self pair");
    std::vector<native_topology::PathInfo> paths(demand.n * demand.n);
    std::vector<bool> seen(paths.size(), false);
    for (unsigned k = 0; k < demand.n * (demand.n - 1); ++k) {
        unsigned s = bounded(token(input), demand.n - 1);
        unsigned d = bounded(token(input), demand.n - 1);
        need(s != d && !seen[s * demand.n + d], "duplicate or self path");
        seen[s * demand.n + d] = true;
        double propagation = number(token(input)), rtt = number(token(input));
        need(propagation >= 0 && rtt >= 0, "path delays must be nonnegative");
        unsigned length = bounded(token(input), count);
        need(length > 0, "non-self path must have a resource");
        std::vector<unsigned> ids;
        std::set<unsigned> unique;
        for (unsigned j = 0; j < length; ++j) {
            unsigned id = bounded(token(input), count - 1);
            need(unique.insert(id).second, "repeated resource in path");
            ids.push_back(id);
        }
        paths[s * demand.n + d] = native_topology::PathInfo(ids, propagation, rtt);
    }
    std::string extra;
    need(!(input >> extra), "trailing input token");
    auto start = Clock::now();
    Topology result = Topology::from_fixed_routes(demand.n, 1, resources, paths);
    setup_us = elapsed(start);
    return result;
}
bool same_plan(const Plan &a, const Plan &b) {
    if (a.engine != b.engine || a.space != b.space || a.stages.size() != b.stages.size() ||
        a.decomposition_steps != b.decomposition_steps ||
        a.candidate_evaluations != b.candidate_evaluations || a.edit_attempts != b.edit_attempts)
        return false;
    for (size_t k = 0; k < a.stages.size(); ++k) {
        const auto &x = a.stages[k];
        const auto &y = b.stages[k];
        if (x.coefficient != y.coefficient || x.flows.size() != y.flows.size())
            return false;
        for (size_t j = 0; j < x.flows.size(); ++j)
            if (x.flows[j].src_gpu != y.flows[j].src_gpu ||
                x.flows[j].dst_gpu != y.flows[j].dst_gpu || x.flows[j].bytes != y.flows[j].bytes)
                return false;
    }
    return true;
}
template <class T> void array(std::ostream &out, const std::vector<T> &values) {
    out << '[';
    for (size_t i = 0; i < values.size(); ++i) {
        if (i)
            out << ',';
        out << values[i];
    }
    out << ']';
}
void output(const std::string &method, const std::string &input, const Demand &d, const Options &o,
            const Plan &p, const Validation &v, const Evaluation &e,
            const std::vector<double> &samples, double setup, double validation,
            double evaluation) {
    auto &out = std::cout;
    out << std::setprecision(std::numeric_limits<long double>::max_digits10);
    uint64_t total = 0;
    for (auto x : d.bytes)
        total += x;
    out << "{\"schema_version\":1,\"method\":" << quoted(method) << ",\"options\":{"
        << "\"engine\":" << quoted(engine_name(o.engine))
        << ",\"space\":" << quoted(space_name(o.space))
        << ",\"global\":" << quoted(global_name(o.global))
        << ",\"local\":" << quoted(local_name(o.local))
        << ",\"pressure_candidate\":" << (o.pressure_candidate ? "true" : "false")
        << ",\"local_edits\":" << (o.local_edits ? "true" : "false")
        << ",\"candidate_budget\":" << o.candidate_budget
        << ",\"edit_attempt_budget\":" << o.edit_attempt_budget
        << ",\"min_seed_service_ratio\":" << o.min_seed_service_ratio
        << ",\"smooth_beta\":" << o.smooth_beta << ",\"delay_alpha\":" << o.delay_alpha
        << ",\"native_unit_bytes\":" << o.native_unit_bytes << "},\"n\":" << d.n
        << ",\"payload_model\":" << quoted(o.space == Space::Bytes ? "integer" : "continuous")
        << ",\"demand_bytes\":" << total << ",\"communication_us\":" << e.communication_us
        << ",\"serialization_sum_us\":" << e.serialization_sum_us
        << ",\"propagation_sum_us\":" << e.propagation_sum_us
        << ",\"execution_stages\":" << p.stages.size()
        << ",\"decomposition_steps\":" << p.decomposition_steps
        << ",\"candidate_evaluations\":" << p.candidate_evaluations
        << ",\"edit_attempts\":" << p.edit_attempts
        << ",\"max_pair_error_bytes\":" << v.max_pair_error_bytes << ",\"planning_samples_us\":";
    array(out, samples);
    out << ",\"topology_setup_us\":" << setup << ",\"validation_us\":" << validation
        << ",\"evaluation_us\":" << evaluation << ",\"resource_total_bytes\":";
    array(out, e.resource_total_bytes);
    out << ",\"stages\":[";
    for (size_t k = 0; k < p.stages.size(); ++k) {
        if (k)
            out << ',';
        out << "{\"coefficient\":" << p.stages[k].coefficient << ",\"flows\":[";
        for (size_t j = 0; j < p.stages[k].flows.size(); ++j) {
            if (j)
                out << ',';
            const auto &f = p.stages[k].flows[j];
            out << "{\"src\":" << f.src_gpu << ",\"dst\":" << f.dst_gpu << ",\"bytes\":" << f.bytes
                << '}';
        }
        out << "],\"alpha_us\":" << e.stages[k].alpha_us
            << ",\"serial_us\":" << e.stages[k].serial_us
            << ",\"total_us\":" << e.stages[k].total_us << ",\"resource_bytes\":";
        array(out, e.stages[k].resource_bytes);
        out << '}';
    }
    out << "],\"build_info\":{\"compiler\":" << quoted(__VERSION__)
        << ",\"cpp_standard\":" << __cplusplus << ",\"git_revision\":" << quoted(BVN_GIT_REVISION)
        << ",\"git_dirty\":" << quoted(BVN_GIT_DIRTY) << ",\"input\":" << quoted(input)
        << ",\"native_scaling\":\"off\",\"coefficient_unit\":"
        << quoted(o.space == Space::Bytes ? "bytes" : "us")
        << ",\"conservation_absolute_tolerance_bytes\":1e-6,\"conservation_relative_tolerance\":"
        << 64 * std::numeric_limits<double>::epsilon() << "}}\n";
}
} // namespace

int main(int argc, char **argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--help") {
            std::cout
                << "bvn_simulator --input CASE --method "
                   "fast-native|partial-endpoint|partial-topology|partial-time|partial-delay\n"
                   "  [--repeat 1..100] [--space bytes|time|delay] [--global "
                   "none|max|smooth|pressure]\n"
                   "  [--local none|stage|square|path] [--pressure 0|1] [--edits 0|1]\n"
                   "  [--candidate-budget C] [--edit-budget E] [--rho X] [--beta X]\n"
                   "  [--delay-alpha X] [--unit-bytes U]\n";
            return 0;
        }
        auto args = arguments(argc, argv);
        Options options = overrides(preset(args.at("--method")), args);
        unsigned repeat = args.count("--repeat") ? bounded(args.at("--repeat"), 100) : 1;
        need(repeat >= 1, "repeat must be in [1,100]");
        Demand demand;
        double setup_us = 0;
        Topology topology = read_case(args.at("--input"), demand, setup_us);
        Plan first;
        std::vector<double> samples;
        for (unsigned i = 0; i < repeat; ++i) {
            auto start = Clock::now();
            Plan next = plan(demand, topology, options);
            samples.push_back(elapsed(start));
            if (!i)
                first = std::move(next);
            else
                need(same_plan(first, next), "repeat produced a different plan");
        }
        auto start = Clock::now();
        Validation validation = validate_schedule(demand, first);
        double validation_us = elapsed(start);
        need(validation.ok, "schedule validation failed: " + validation.message);
        start = Clock::now();
        Evaluation evaluation = evaluate_schedule(topology, first);
        double evaluation_us = elapsed(start);
        output(args.at("--method"), args.at("--input"), demand, options, first, validation,
               evaluation, samples, setup_us, validation_us, evaluation_us);
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "bvn_simulator: " << e.what() << '\n';
        return 1;
    }
}
