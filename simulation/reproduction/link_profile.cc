// Only native link factories are parameterized. No scheduler source is edited.
// ============================================================================
// 通过“同名符号覆盖”实现 profile：核心库仍提供真实工厂（前缀 upstream_），
// 本文件在链接时提供 get_*_link_info 包装，因此调度器无需任何改动。
// 注意这是进程级全局状态，配置一次后所有后续调度器共用。
// ============================================================================
#include "link_profile.h"
#include <cmath>
#include <stdexcept>

// 核心库中的真实链路工厂（由链接器解析）。
link_info_t upstream_get_inter_link_info(INTER_LINK_TYPE);
link_info_t upstream_get_intra_link_info(INTRA_LINK_TYPE);
// 默认保持上游行为；paper 模式使用 400/3600 Gbps，alpha_scale=1。
namespace {std::string mode="upstream-default";double out_bw=400,in_bw=3600,alpha_factor=1;}
// 校验 profile 名与参数。非 paper 的“原始” profile 不允许改 alpha，
// 以避免在同一组实验里混入未声明的缩放。
void configure_link_profile(const std::string& name,double inter,double intra,double scale) {
  if(name!="paper"&&name!="upstream-default"&&name!="upstream-400")throw std::invalid_argument("profile not registered");
  if(!std::isfinite(inter)||!std::isfinite(intra)||inter<=0||intra<=0||!std::isfinite(scale)||scale<0)
    throw std::invalid_argument("invalid bandwidth/alpha scale");
  if(name!="paper"&&scale!=1)throw std::invalid_argument("original profile cannot change alpha");
  mode=name;out_bw=inter;in_bw=intra;alpha_factor=scale;
}
// server 间链路：paper 下 beta(us/MB)=8000/Gbps；其余 profile 原样返回。
link_info_t get_inter_link_info(INTER_LINK_TYPE type) {
  if(type!=ETHER100&&type!=ETHER400)throw std::invalid_argument("unsupported inter enum");
  auto result=upstream_get_inter_link_info(type);
  if(mode=="paper"){result.beta=8000./out_bw;result.alpha*=alpha_factor;}
  return result;
}
// server 内链路：同一套规则，带宽默认 3600 Gbps（NVLink 等效）。
link_info_t get_intra_link_info(INTRA_LINK_TYPE type) {
  if(type!=H100)throw std::invalid_argument("unsupported intra enum");
  auto result=upstream_get_intra_link_info(type);
  if(mode=="paper"){result.beta=8000./in_bw;result.alpha*=alpha_factor;}
  return result;
}
