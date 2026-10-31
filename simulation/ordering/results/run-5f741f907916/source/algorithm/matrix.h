#pragma once

// ============================================================================
// Matrix：整个仿真器使用的稠密方阵（元素是无符号整数），外加
// "缩放双随机矩阵"（scaled doubly stochastic matrix, SDSM）的元信息。
//
// 为什么需要 sdsm_info？
//   FAST 要把 server 间的流量矩阵做 Birkhoff-von Neumann 分解，拆成
//   "若干置换矩阵 × 权重（frequency）"。这个分解的前提是矩阵每一行、
//   每一列的流量之和都相等，也就是所谓双随机矩阵。
//   get_sdsm_info() 会扫描一遍矩阵：
//     - max_row_col_sum 记录所有行/列和中最大的那个值；
//     - non_max_row / non_max_col 记录还没达到这个最大值的行和列；
//     - is_sdsm 本意是标记当前矩阵是否已经满足"行和 = 列和 = 最大值"。
//       但它的计数方式有偏差：即使矩阵已经完全平衡，也常常是 false
//       （详见 matrix.cpp）。判断/设置 SDSM 状态请以
//       to_scaled_doubly_stochastic_matrix() 补齐后的结果为准。
//   随后的 to_scaled_doubly_stochastic_matrix() 就利用这些列表，
//   往"缺口"位置补流量，把矩阵凑成双随机矩阵。
//
// 关于 unit（单位）：
//   矩阵里的 uint 只是计数单位。unit 是矩阵自带的"每个数值代表多少单位"的
//   标记，scaling 时数值整体缩小、unit 相应放大；但当前仿真流程并不直接读
//   这个字段，时间计算用的是 FastAll2All 里的 MB_per_unit 和 scaling_factor。
//   scaling 在"分解精度"和"置换阶段数"之间的折中详见 FastAll2All。
// ============================================================================

#include <iostream>
#include <vector>
#include "define.h"
using namespace std;

// 记录某一行的行号/列号以及它当前的总和，用于记录"未达到最大和"的行列。
struct row_col_info_t {
    uint idx;
    uint sum;
};

// 双随机矩阵检查结果。命名中的 sdsm = scaled doubly stochastic matrix。
struct sdsm_info_t{
    bool is_sdsm;                     // 设计意图：所有行/列和都等于 max_row_col_sum；实际计数有偏差，不可完全依赖
    uint max_row_col_sum;             // 当前矩阵的最大行和/列和，也就是补齐的目标值
    vector<struct row_col_info_t> non_max_row;  // 行和小于最大值的行
    vector<struct row_col_info_t> non_max_col;  // 列和小于最大值的列
};


class Matrix {
private:
    uint ** data;   // 二维数组，data[i][j] 表示第 i 行第 j 列的元素
    uint dim;       // 方阵维度（matrix 始终是 dim × dim）
    uint unit;      // 单位标记：scaling 后会被放大；当前计时模型未直接使用
public:
    struct sdsm_info_t sdsm_info;
    Matrix(): data(NULL), dim(0), unit(1){sdsm_info.is_sdsm=false;sdsm_info.max_row_col_sum=0;};
    Matrix(uint _dim, uint _unit = 1);                 // 构造 dim×dim 的全零矩阵
    Matrix(uint** _data, uint _dim, uint _unit = 1);   // 从二维数组深拷贝
    Matrix(uint* _data, uint _dim, uint _unit = 1);    // 从行优先的一维数组深拷贝
    Matrix(Matrix * mat);                              // 拷贝构造（深拷贝）
    ~Matrix();
    void copy(Matrix * mat);                           // 用另一个矩阵覆盖自己（必要时重新分配内存）
    void copy(uint * _data, uint _dim);                // 用行优先一维数组覆盖自己
    bool valid() {return dim > 0 && data != NULL;}
    bool valid_sdsm() {return dim > 0 && data != NULL && sdsm_info.is_sdsm;}
    uint get_dim() {return dim;}
    uint get_unit() {return unit;}
    uint get(uint x, uint y);                          // 取值，越界会打印日志并返回 0
    bool set(uint val, uint x, uint y);                // 赋值
    bool add(uint val, uint x, uint y);                // 累加
    bool subtract(uint val, uint x, uint y);           // 累减
    void scale(uint factor);                           // 每个元素向上取整除以 factor，并把 unit 乘以 factor
    bool equal_to(Matrix * mat);                       // 逐元素比较
    void get_sdsm_info();                              // 重新计算行/列和以及 SDSM 状态
    void print();
};