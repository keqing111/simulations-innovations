#include "ExternalStageBarrier.h"
#include <cassert>
#include <stdexcept>

int main() {
  ExternalStageBarrier gate({{0,0}, {1,0}, {2,3}, {3,3}});
  int calls = 0;
  auto invoke = [](std::vector<std::function<void()>> ready) {
    for (auto& callback : ready) callback();
  };
  invoke(gate.enqueue(2, [&] { calls += 100; }));
  invoke(gate.enqueue(0, [&] { calls += 1; }));
  invoke(gate.enqueue(1, [&] { calls += 10; }));
  assert(calls == 11);
  invoke(gate.complete(1));
  assert(calls == 11);
  invoke(gate.complete(0));
  assert(calls == 111);
  invoke(gate.enqueue(3, [&] { calls += 1000; }));
  assert(calls == 1111);
  invoke(gate.complete(2));
  invoke(gate.complete(3));
  assert(gate.finished());
  bool duplicate = false;
  try { gate.complete(0); } catch (const std::runtime_error&) { duplicate = true; }
  assert(duplicate);
  ExternalStageBarrier invalid({{0,0}, {1,1}});
  bool premature = false;
  try { invalid.complete(1); } catch (const std::runtime_error&) { premature = true; }
  assert(premature);
}
