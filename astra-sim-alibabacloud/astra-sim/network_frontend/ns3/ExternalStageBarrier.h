#ifndef SIMAI_EXTERNAL_STAGE_BARRIER_H
#define SIMAI_EXTERNAL_STAGE_BARRIER_H

#include <algorithm>
#include <climits>
#include <fstream>
#include <functional>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

// Single-threaded ns-3 coordinator. All expected flows are known before Run();
// only current-stage sends are released. No distributed barrier cost is added.
class ExternalStageBarrier {
 public:
  using Callback = std::function<void()>;
  explicit ExternalStageBarrier(const std::map<int, int>& stages) : stages_(stages) {
    for (const auto& item : stages_) {
      if (item.first < 0 || item.second < 0)
        throw std::runtime_error("negative external stage entry");
      ++remaining_[item.second];
    }
    current_ = remaining_.begin();
  }

  static std::map<int, int> load(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open external stage sidecar: " + path);
    std::map<int, int> stages;
    std::string line;
    while (std::getline(input, line)) {
      if (!line.empty() && line.back() == '\r') line.pop_back();
      const auto comma = line.find(',');
      if (comma == std::string::npos || line.find(',', comma + 1) != std::string::npos)
        throw std::runtime_error("stage sidecar requires flow_id,stage_id rows");
      auto integer = [](const std::string& token) {
        if (token.empty() || !std::all_of(token.begin(), token.end(),
            [](char c) { return c >= '0' && c <= '9'; }))
          throw std::runtime_error("stage entries must be nonnegative integers");
        const auto value = std::stoull(token);
        if (value > INT_MAX) throw std::runtime_error("stage entry exceeds INT_MAX");
        return static_cast<int>(value);
      };
      if (!stages.emplace(integer(line.substr(0, comma)),
                          integer(line.substr(comma + 1))).second)
        throw std::runtime_error("duplicate flow in external stage sidecar");
    }
    int expected = 0;
    for (const auto& item : stages)
      if (item.first != expected++)
        throw std::runtime_error("stage sidecar must cover consecutive flow IDs");
    return stages;
  }

  std::vector<Callback> enqueue(int id, Callback callback) {
    const int stage = stage_of(id);
    if (!queued_.insert(id).second)
      throw std::runtime_error("external stage flow registered twice");
    if (current_ != remaining_.end() && stage == current_->first) {
      started_.insert(id);
      return {std::move(callback)};
    }
    pending_[stage].emplace_back(id, std::move(callback));
    return {};
  }

  std::vector<Callback> complete(int id) {
    const int stage = stage_of(id);
    if (!started_.count(id) || completed_.count(id) ||
        current_ == remaining_.end() || stage != current_->first)
      throw std::runtime_error("duplicate or premature external stage completion");
    completed_.insert(id);
    if (--current_->second != 0) return {};
    ++current_;  // std::map skips stages containing only virtual traffic.
    std::vector<Callback> ready;
    if (current_ != remaining_.end()) {
      auto found = pending_.find(current_->first);
      if (found != pending_.end()) {
        for (auto& item : found->second) {
          started_.insert(item.first);
          ready.push_back(std::move(item.second));
        }
        pending_.erase(found);
      }
    }
    return ready;
  }

  bool finished() const { return current_ == remaining_.end(); }
  int stage_of(int id) const {
    const auto found = stages_.find(id);
    if (found == stages_.end())
      throw std::runtime_error("external flow missing from stage sidecar");
    return found->second;
  }

 private:
  std::map<int, int> stages_;
  std::map<int, std::size_t> remaining_;
  std::map<int, std::size_t>::iterator current_;
  std::map<int, std::vector<std::pair<int, Callback>>> pending_;
  std::set<int> queued_, started_, completed_;
};
#endif
