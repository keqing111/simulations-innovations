#include "algorithm/all2all.h"
#include "topology/model.h"
#include <iostream>
#include <vector>

int main() {
    // 3 servers; only 0<->1 cross traffic; row/col 2 are empty, so
    // doubly-stochastic stuffing should add a (2,2) self edge.
    unsigned data[9] = {0u, 100000000u, 0u,
                        100000000u, 0u, 0u,
                        0u, 0u, 0u};
    Matrix mat(data, 3);
    FastAll2All app(&mat, 2, ETHER400, H100, 1e-6);
    app.to_scaled_doubly_stochastic_matrix();
    std::cout << "SDS matrix:\n";
    app.print();
    std::vector<uint64_t> pending(3 * 3 * 2, 0);
    pending[(0 * 3 + 1) * 2 + 0] = 100000000;
    pending[(0 * 3 + 1) * 2 + 1] = 100000000;
    pending[(1 * 3 + 0) * 2 + 0] = 100000000;
    pending[(1 * 3 + 0) * 2 + 1] = 100000000;
    // same-server intrinsic on server 2: GPU0 -> GPU1
    pending[(2 * 3 + 2) * 2 + 1] = 100000000;
    native_topology::Topology top(3, 2, {0, 1, 2}, 400, 400, 100, 5000);
    app.decompose(ScheduleMode::Native, top, pending);
    std::cout << "stages=" << app.p_sets.size() << "\n";
    for (size_t k = 0; k < app.p_sets.size(); ++k) {
        std::cout << "stage " << k << " freq=" << app.p_sets[k].get_freq() << " mp:";
        for (auto &e : app.p_sets[k].mp) {
            std::cout << " " << e.first << "->" << e.second;
            if (e.first == e.second) std::cout << "(self)";
        }
        std::cout << "\n";
    }
    return 0;
}
