#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

// ============================================================================
// topology/ 的资源级网络模型。
//
// 与算法核心使用的 alpha-beta 模型不同，这里把一次 server 间传输显式建模成
// 一串“有向资源”（server 出口、gateway 出口、WAN 方向、目的 gateway、目的
// server 入口），每条资源有独立带宽。评估一次传输时：
//   alpha  = 路径上所有固定延迟之和（同 DC 1.4us，跨 DC = wan_us + 2.8us）
//   serial = 每条资源上所有共享该资源的字节 / 该资源带宽 的最大值
//   total  = alpha + serial
// 因此同一条 WAN 上的多个流会串行叠加，而不同方向/不同资源可以并行。
// 这是一个无拥塞、无 ACK、全双工的静态模型，用于比较调度策略的相对优劣。
//
// 命名：
//   n/g        server 数与每 server GPU 数
//   dc[s]      server s 所在的数据中心编号
//   Transfer   一条已聚合的 server 对流量（src/dst 均为 server 编号）
//   Cost       一次 evaluate 的分解结果，便于输出 alpha/serial/各资源字节
// ============================================================================

namespace native_topology {
struct ResourceInfo {std::string name; double bandwidth_gbps;};
struct PathInfo {     //path 表示一个通信对的路径？包含路径上的链路、传播时间、往返时间
  std::vector<unsigned> resource_ids;
  double propagation_us, rtt_us;
  PathInfo(std::vector<unsigned> ids={},double propagation=0,double rtt=0)
    :resource_ids(std::move(ids)),propagation_us(propagation),rtt_us(rtt) {}
};
struct GpuTransfer {unsigned src_gpu,dst_gpu; double bytes;};
struct GpuStageCost {
  double alpha_us=0,serial_us=0,total_us=0;
  std::vector<double> resource_bytes;
};
struct Transfer {unsigned src,dst;uint64_t bytes;};
// 一次拓扑评估的完整结果。bytes/bandwidth_gbps 以资源名称为键，
// 方便 JSON 直接展开；total_us 应等于 alpha_us + serial_us。
struct Cost {
  double alpha_us=0,serial_us=0,total_us=0;
  std::map<std::string,uint64_t> bytes;
  std::map<std::string,double> bandwidth_gbps;
};
// 物理拓扑。构造时预展开所有 n×n 条 server 对路径，evaluate 只做查表与
// 资源累加，因此评估本身不分配复杂对象，可被 Planner 反复调用。
class Topology {
 public:
  unsigned n,g;
  std::vector<int> dc;
  // server_bw/gateway_bw 单位为 Gbps；wan_bw 为每条 WAN 方向带宽；
  // wan_us 为跨 DC 的固定延迟（同 DC 固定 1.4us）。
  Topology(unsigned n,unsigned g,std::vector<int> dc,double server_bw=400,
      double gateway_bw=400,double wan_bw=100,double wan_us=5000);
  // 均匀模型：所有 server 对同带宽、同延迟，仅用于解析对照/下界。
  // beta_us_MB 是每 MB 的串行时间，alpha_us 是固定延迟。
  static Topology homogeneous(unsigned n,unsigned g,double beta_us_MB,double alpha_us);
  static Topology from_fixed_routes(unsigned n,unsigned g,
      const std::vector<ResourceInfo>& resources,const std::vector<PathInfo>& paths);
  const PathInfo& gpu_path(unsigned src_gpu,unsigned dst_gpu) const;
  const ResourceInfo& resource(unsigned id) const;
  unsigned resource_count() const {return resources_.size();}
  double path_min_bandwidth_gbps(unsigned src_gpu,unsigned dst_gpu) const;
  GpuStageCost evaluate_gpu(const std::vector<GpuTransfer>& transfers) const;
  // 对一组 server 级流量计算拓扑耗时；同一资源上的字节会先聚合再除以带宽。
  Cost evaluate(const std::vector<Transfer>& transfers) const;
  // 路径固定延迟（不含串行传输时间）。
  double path_alpha(unsigned src,unsigned dst) const;
  // 路径经过的资源名称序列，顺序与 evaluate 内部累加顺序一致。
  std::vector<std::string> path(unsigned src,unsigned dst) const;
  std::map<std::string,double> bandwidths; // 资源名 -> Gbps（含 WAN）
 private:
  Topology(unsigned n,unsigned g);
  void cache_legacy_paths();
  void validate_gpu_paths();
  std::vector<ResourceInfo> resources_;
  std::vector<PathInfo> gpu_paths_;
  std::vector<double> path_min_bandwidth_gbps_;
  bool legacy_server_view_=true;
  std::vector<std::vector<std::string>> routes; // [src*n+dst] 资源名序列
  std::vector<double> alphas;                  // [src*n+dst] 固定延迟
};
}
