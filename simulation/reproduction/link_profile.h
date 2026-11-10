#pragma once
#include "config.h"
#include <string>

// ============================================================================
// 链路 profile 配置。
//
// reproduction 需要在不修改核心源码的前提下，比较论文参数与上游默认参数。
// 方式：本文件定义同名 get_inter/intra_link_info，内部先调用
// upstream_get_*（核心库原实现），再按所选 profile 覆盖 beta/alpha。
//   upstream-default / upstream-400：完全使用上游实现，忽略覆盖值；
//   paper：beta = 8000 / Gbps（即 us/MB），alpha 乘以 alpha_scale。
// 必须在创建任何 GlobalScheduler 之前调用一次。
// ============================================================================
void configure_link_profile(const std::string& name,double inter_gbps,double intra_gbps,double alpha_scale);
