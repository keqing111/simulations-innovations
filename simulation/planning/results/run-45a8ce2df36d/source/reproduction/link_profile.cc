// Only native link factories are parameterized. No scheduler source is edited.
#include "link_profile.h"
#include <cmath>
#include <stdexcept>

link_info_t upstream_get_inter_link_info(INTER_LINK_TYPE);
link_info_t upstream_get_intra_link_info(INTRA_LINK_TYPE);
namespace {std::string mode="upstream-default";double out_bw=400,in_bw=3600,alpha_factor=1;}
void configure_link_profile(const std::string& name,double inter,double intra,double scale) {
  if(name!="paper"&&name!="upstream-default"&&name!="upstream-400")throw std::invalid_argument("profile not registered");
  if(!std::isfinite(inter)||!std::isfinite(intra)||inter<=0||intra<=0||!std::isfinite(scale)||scale<0)
    throw std::invalid_argument("invalid bandwidth/alpha scale");
  if(name!="paper"&&scale!=1)throw std::invalid_argument("original profile cannot change alpha");
  mode=name;out_bw=inter;in_bw=intra;alpha_factor=scale;
}
link_info_t get_inter_link_info(INTER_LINK_TYPE type) {
  if(type!=ETHER100&&type!=ETHER400)throw std::invalid_argument("unsupported inter enum");
  auto result=upstream_get_inter_link_info(type);
  if(mode=="paper"){result.beta=8000./out_bw;result.alpha*=alpha_factor;}
  return result;
}
link_info_t get_intra_link_info(INTRA_LINK_TYPE type) {
  if(type!=H100)throw std::invalid_argument("unsupported intra enum");
  auto result=upstream_get_intra_link_info(type);
  if(mode=="paper"){result.beta=8000./in_bw;result.alpha*=alpha_factor;}
  return result;
}
