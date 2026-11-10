// Reuse the tested native bridge verbatim; only substitute its planner class.
// ============================================================================
// bottleneck 的可执行入口：不复制拓扑实验驱动，而是把 topology/main.cc
// 原样 #include 进来，只把其中的 Planner 标识符宏替换成 CachedPlanner。
// 这样两个实验共享完全相同的输入解析、钩子安装、JSON 输出与校验逻辑，
// 唯一差异就是 Birkhoff 每轮的匹配调整实现。
//
// 链接模型仍复用核心全局工厂（get_inter/intra_link_info），但通过
// configure_link_profile 可选择 paper/upstream 等 profile；默认 paper。
// FAST_BOTTLENECK_PROFILE 选择链路 profile，
// FAST_BOTTLENECK_VARIANT  选择 reference/cached/bounded（见 cached.cpp）。
// ============================================================================
#include "matching.h"
#include "cached.h"
#include "link_profile.h"
#define Planner CachedPlanner
#include <cstdlib>
// 这两个是核心库导出的真实工厂；这里声明后重定义同名包装，注入 profile。
link_info_t profile_get_inter_link_info(INTER_LINK_TYPE);
link_info_t profile_get_intra_link_info(INTRA_LINK_TYPE);
// 只在首次调用时读取环境变量并配置链路 profile，之后进程内保持不变。
static void configure_once(){
  static bool ready=[](){
    const char* profile=std::getenv("FAST_BOTTLENECK_PROFILE");
    configure_link_profile(profile?profile:"paper",400,3600,1);return true;
  }();
  (void)ready;
}
// 用带 profile 的版本覆盖核心符号，使被 include 的 topology/main.cc 自动生效。
link_info_t get_inter_link_info(INTER_LINK_TYPE type){configure_once();return profile_get_inter_link_info(type);}
link_info_t get_intra_link_info(INTRA_LINK_TYPE type){configure_once();return profile_get_intra_link_info(type);}
// Keep ::main as main: its implicit success return is only valid for that name.
// 这里 include 的是 .cc 而不是头文件：topology/main.cc 的 main() 会成为
// 本翻译单元的入口，且其末尾依赖 main 的隐式 return 0，不能改成别的名字。
#include "../topology/main.cc"
#undef Planner
