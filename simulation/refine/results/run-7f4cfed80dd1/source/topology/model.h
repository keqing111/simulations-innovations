#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace native_topology {
struct Transfer {unsigned src,dst;uint64_t bytes;};
struct Cost {
  double alpha_us=0,serial_us=0,total_us=0;
  std::map<std::string,uint64_t> bytes;
  std::map<std::string,double> bandwidth_gbps;
};
class Topology {
 public:
  unsigned n,g;
  std::vector<int> dc;
  Topology(unsigned n,unsigned g,std::vector<int> dc,double server_bw=400,
      double gateway_bw=400,double wan_bw=100,double wan_us=5000);
  static Topology homogeneous(unsigned n,unsigned g,double beta_us_MB,double alpha_us);
  Cost evaluate(const std::vector<Transfer>& transfers) const;
  double path_alpha(unsigned src,unsigned dst) const;
  std::vector<std::string> path(unsigned src,unsigned dst) const;
  std::map<std::string,double> bandwidths;
 private:
  std::vector<std::vector<std::string>> routes;
  std::vector<double> alphas;
};
}
