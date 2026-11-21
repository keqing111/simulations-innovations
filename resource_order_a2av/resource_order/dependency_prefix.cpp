// Exact prefix reuse for dependency_native.cpp. No new timing model.
// Build separately; never overwrite a library mapped by a running experiment.
#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

namespace {
struct Context {
  int n, groups;
  std::vector<int> cover, observations;
  std::vector<double> costs, send, receive, maxima, records;
  Context(int size, int count, const int* rows, const double* times)
      : n(size), groups(count), cover(rows, rows + size * count), observations(count + 1),
        costs(times, times + 3 * size * size),
        send((count + 1) * size), receive((count + 1) * size), maxima(count + 1) {
    for (int k = 0; k < groups; ++k) {
      std::copy_n(send.data() + k*n, n, send.data() + (k+1)*n);
      std::copy_n(receive.data() + k*n, n, receive.data() + (k+1)*n);
      double maximum = maxima[k];
      observations[k+1] = observations[k];
      for (int src = 0; src < n; ++src) {
        const int dst = cover[k*n+src];
        if (dst < 0) continue;
        const double* edge = costs.data() + 3*(src*n+dst);
        if (edge[2] < 0.) continue;
        ++observations[k+1];
        const double done = std::max(send[(k+1)*n+src], receive[(k+1)*n+dst]+edge[1])+edge[0]+edge[2];
        receive[(k+1)*n+dst] = done;
        send[(k+1)*n+src] = done+edge[1];
        // Store exactly the first value returned for each cutoff range.
        if (records.empty() || done > maximum) records.push_back(std::max(maximum, done));
        maximum = std::max(maximum, done);
      }
      maxima[k+1] = maximum;
    }
  }
  double original_result(double cutoff) const {
    const auto found = std::lower_bound(records.begin(), records.end(), cutoff);
    return found == records.end() ? maxima.back() : *found;
  }
};
}

extern "C" void* cover_prefix_create(int n, int groups, const int* rows, const double* costs) {
  if (n <= 0 || groups <= 0 || !rows || !costs) return nullptr;
  try { return new Context(n, groups, rows, costs); }
  catch (...) { return nullptr; }
}

extern "C" void cover_prefix_destroy(void* pointer) {
  delete static_cast<Context*>(pointer);
}

extern "C" double cover_prefix_evaluate(void* pointer, int a, int b,
                                         const unsigned char* mask, double cutoff) {
  const auto* c = static_cast<const Context*>(pointer);
  if (!c || a < 0 || b < 0 || a >= c->groups || b >= c->groups || !mask)
    return std::numeric_limits<double>::quiet_NaN();
  const int first = std::min(a,b), last = std::max(a,b), n = c->n;
  double maximum = c->maxima[first];
  if (maximum >= cutoff && c->observations[first] > 0) return c->original_result(cutoff);
  std::vector<double> send(c->send.begin()+first*n, c->send.begin()+(first+1)*n);
  std::vector<double> receive(c->receive.begin()+first*n, c->receive.begin()+(first+1)*n);
  for (int k = first; k < c->groups; ++k) {
    for (int src = 0; src < n; ++src) {
      int row = k;
      if (mask[src]) {
        if (k == a) row = b;
        else if (k == b) row = a;
      }
      const int dst = c->cover[row*n+src];
      if (dst < 0) continue;
      const double* edge = c->costs.data()+3*(src*n+dst);
      if (edge[2] < 0.) continue;
      const double done = std::max(send[src],receive[dst]+edge[1])+edge[0]+edge[2];
      receive[dst] = done;
      send[src] = done+edge[1];
      maximum = std::max(maximum,done);
      if (maximum >= cutoff) return maximum;
    }
    // Exact equality, not an approximate convergence tolerance.
    if (k >= last && maximum == c->maxima[k+1] &&
        std::equal(send.begin(),send.end(),c->send.begin()+(k+1)*n) &&
        std::equal(receive.begin(),receive.end(),c->receive.begin()+(k+1)*n))
      return c->original_result(cutoff);
  }
  return maximum;
}
