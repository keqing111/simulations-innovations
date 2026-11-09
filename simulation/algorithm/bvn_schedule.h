#pragma once
#include "topology/model.h"
#include <cstdint>
#include <string>
#include <vector>

namespace bvn {
using Topology = native_topology::Topology;
using Flow = native_topology::GpuTransfer;
using StageCost = native_topology::GpuStageCost;
struct Demand {
    unsigned n;
    std::vector<uint64_t> bytes;
};
enum class Engine { FastNative, Partial };
enum class Space { Bytes, SerializationTime, DelayAdjustedTime };
enum class GlobalRule { None, MaxResidual, SmoothResidual, Pressure };
enum class LocalRule { None, StageTime, SquaredWork, MaxPathTime };
struct Options {
    Engine engine = Engine::Partial;
    Space space = Space::Bytes;
    GlobalRule global = GlobalRule::None;
    LocalRule local = LocalRule::None;
    bool pressure_candidate = false, local_edits = false;
    unsigned candidate_budget = 1, edit_attempt_budget = 0;
    double min_seed_service_ratio = 1, smooth_beta = 0, delay_alpha = 0;
    uint64_t native_unit_bytes = 1;
};
struct Stage {
    std::vector<Flow> flows;
    long double coefficient = 0;
};
struct Plan {
    Engine engine = Engine::Partial;
    Space space = Space::Bytes;
    std::vector<Stage> stages;
    uint64_t decomposition_steps = 0, candidate_evaluations = 0, edit_attempts = 0;
};
struct Validation {
    bool ok = false;
    long double max_pair_error_bytes = 0;
    std::string message;
};
struct Evaluation {
    double communication_us = 0, serialization_sum_us = 0, propagation_sum_us = 0;
    std::vector<StageCost> stages;
    std::vector<double> resource_total_bytes;
};
// Time-space conservation uses max(1e-6 byte, 64*double epsilon*pair demand).
void validate_options(const Demand &, const Topology &, const Options &);
Plan plan(const Demand &, const Topology &, const Options &);
Validation validate_schedule(const Demand &, const Plan &);
Evaluation evaluate_schedule(const Topology &, const Plan &);
} // namespace bvn
