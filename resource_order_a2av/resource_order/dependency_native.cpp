// Exact implementation of cover.py::dependency_bound, not a simulator.
// No fast-math: keep the same addition order as the Python reference.
#include <algorithm>
#include <vector>

extern "C" int cover_partial_abi() { return 1; }

extern "C" double cover_dependency_bound(
    int n, int groups, const int* cover, const double* costs,
    int a, int b, const unsigned char* cycle, double cutoff) {
  std::vector<double> send(n, 0.0), receive(n, 0.0);
  double maximum = 0.0;
  for (int k = 0; k < groups; ++k) {
    for (int src = 0; src < n; ++src) {
      int row = k;
      if (cycle[src]) {
        if (k == a) row = b;
        else if (k == b) row = a;
      }
      const int dst = cover[row * n + src];
      if (dst < 0) continue;  // partial matching: no message or endpoint update
      const double* edge = costs + 3 * (src * n + dst);
      if (edge[2] < 0.0) continue;  // absent message, no control or data work
      const double forward = edge[0], reverse = edge[1], serial = edge[2];
      const double done = std::max(send[src], receive[dst] + reverse) + forward + serial;
      receive[dst] = done;
      send[src] = done + reverse;
      maximum = std::max(maximum, done);
      if (maximum >= cutoff) return maximum;
    }
  }
  return maximum;
}
