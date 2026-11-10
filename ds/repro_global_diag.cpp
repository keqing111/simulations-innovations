#include "scheduler/global.h"
#include "scheduler/local.h"
#include "topology/model.h"
#include <iostream>
#include <memory>
#include <vector>

static void print_stage(const pipeline_hooks_t &h) {
    for (size_t k = 0; k < h.stages.size(); ++k) {
        std::cout << "stage " << k << " freq=" << h.stages[k].get_freq() << " mp:";
        for (auto &e : h.stages[k].mp) {
            std::cout << " " << e.first << "->" << e.second;
            if (e.first == e.second) std::cout << "(self)";
        }
        std::cout << " payload:";
        for (unsigned i = 0; i < h.payload_units[k].size(); ++i)
            if (h.payload_units[k][i]) std::cout << " [" << i << "]=" << h.payload_units[k][i];
        std::cout << "\n";
    }
}

int main() {
    const unsigned N = 3, G = 2;
    const unsigned dim = N * G;
    std::vector<unsigned> data(dim * dim, 0);
    // cross 0 -> 1: every source GPU sends 100M to every destination GPU
    for (unsigned i = 0; i < G; ++i)
        for (unsigned j = 0; j < G; ++j) {
            data[(0 * G + i) * dim + (1 * G + j)] = 100000000;
            data[(1 * G + i) * dim + (0 * G + j)] = 100000000;
        }
    // same-server traffic on server 2: GPU0 -> GPU1
    data[(2 * G + 0) * dim + (2 * G + 1)] = 100000000;

    std::vector<std::unique_ptr<LocalScheduler>> owners;
    std::vector<LocalScheduler *> locals;
    for (unsigned s = 0; s < N; ++s) {
        owners.emplace_back(new LocalScheduler(data.data() + s * G * dim, G, N, s, H100));
        locals.push_back(owners.back().get());
    }
    native_topology::Topology top(N, G, {0, 1, 2}, 400, 400, 100, 5000);
    GlobalScheduler global(N, G, locals, ETHER400, H100, false, 1e-6);
    global.set_schedule(ScheduleMode::Native, &top);

    pipeline_hooks_t h;
    auto result = global.pipeline2(1e-6, &balance_alpha_beta, &h);
    std::cout << "t=" << result.t << " balance=" << h.balance_us
              << " intrinsic=" << h.intrinsic_us << "\n";
    print_stage(h);
    std::cout << "dispatch:";
    for (double x : h.dispatch_us) std::cout << " " << x;
    std::cout << "\n";
    return 0;
}
