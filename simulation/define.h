#pragma once

// ============================================================================
// 全局编译期常量与日志宏。
// 这个头文件被几乎所有模块包含，只放"到处都要用"的开关和阈值，
// 不放任何业务结构，避免模块之间产生不必要的依赖。
// ============================================================================

#include <stdio.h>

// 编译时定义 NOLOG 可以彻底关闭日志（例如跑大规模 benchmark 时减少 I/O 干扰）。
#ifdef NOLOG
#define LOG(...)
#else
#define LOG(fmt, ...)                   \
  {                                           \
    printf("LOG: " fmt "\n", ##__VA_ARGS__); \
    fflush(stdout);                       \
  }
#endif

#define MAX(x, y) ((x > y) ? x : y)
#define MIN(x, y) ((x < y) ? x : y)

// scaling 因子搜索区间：[1, 1000000]，步长 2（从 1 开始，只检查奇数因子）。
// 因子越大，矩阵被压缩得越狠，置换阶段越少，但量化误差也越大。
#define SCALING_FACTOR_MIN 1
#define SCALING_FACTOR_MAX 1000000
#define SCALING_FACTOR_STEP 2

// limit_max_sum() 使用的硬阈值：缩放后最大行/列和不超过这个值才接受。
#define MAX_SUM_LIMIT 100
// benchmark 结果文本的输出目录（相对于运行目录），运行前需要先创建。
#define BENCHMARK_DIR "benchmark/"