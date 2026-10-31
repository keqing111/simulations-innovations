#include <iostream>
#include <stdio.h>
#include <iomanip>
#include "matrix.h"

using namespace std;

// ============================================================================
// Matrix 的实现。这里都是很直白的二维数组操作：
//   - 构造/拷贝都是深拷贝，避免多个 FastAll2All 之间共享同一块流量数据；
//   - get/add/subtract 都带边界检查（越界只打日志，不抛异常）；
//   - scale() 与 get_sdsm_info() 是 FAST 算法会用到的两个特殊操作。
// ============================================================================

// 构造一个 dim×dim 的全零矩阵。
Matrix::Matrix(uint _dim, uint _unit){
    unit = _unit;
    dim = _dim;
    sdsm_info.max_row_col_sum = 0;
    sdsm_info.is_sdsm = false;
    data = new uint*[_dim];
    for (uint i = 0; i < _dim; i++){
        data[i] = new uint[_dim];
        for (uint j = 0; j < _dim; j++){
            data[i][j] = 0;
        }
    }
}

// 从行优先的一维数组构造矩阵：_data[i * dim + j] 对应 data[i][j]。
Matrix::Matrix(uint* _data, uint _dim, uint _unit){
    unit = _unit;
    dim = _dim;
    sdsm_info.max_row_col_sum = 0;
    sdsm_info.is_sdsm = false;
    data = new uint*[_dim];
    uint idx = 0;
    for (uint i = 0; i < _dim; i ++){
        data[i] = new uint[_dim];
        for (uint j = 0; j < _dim; j++){
            data[i][j] = _data[idx];
            idx ++;
        }
    }
}

Matrix::Matrix(uint** _data, uint _dim, uint _unit){
    unit = _unit;
    dim = _dim;
    sdsm_info.max_row_col_sum = 0;
    sdsm_info.is_sdsm = false;
    data = new uint*[_dim];
    for (uint i = 0; i < _dim; i ++){
        data[i] = new uint[_dim];
        for (uint j = 0; j < _dim; j++){
            data[i][j] = _data[i][j];
        }
    }
}


// hard copy - initiate a matrix with an exisiting matrix
Matrix::Matrix(Matrix * mat){
    dim = mat->get_dim();
    unit = mat->get_unit();
    sdsm_info = mat->sdsm_info;
    data = new uint*[dim];
    for (uint i = 0; i < dim; i++){
        data[i] = new uint[dim];
        for (uint j = 0; j < dim; j++){
            data[i][j] = mat->get(i,j);
        }
    }
}

// hard copy
void Matrix::copy(Matrix * mat){
    uint source_dim = mat->get_dim();
    unit = mat->get_unit();
    sdsm_info = mat->sdsm_info;
    if (source_dim == 0){
        LOG("error when assigning an empty matrix");
        return;
    }
    if (dim > 0 && data != NULL && dim != source_dim){
        // matrix dimension different - release memory first
        for (uint i = 0; i < dim; i++){
            delete[] data[i];
        }
        delete[] data;
    }
    if (dim != source_dim){
        data = new uint*[source_dim];
        for (uint i = 0; i < source_dim; i++){
            data[i] = new uint[source_dim];
        }
    }
    dim = source_dim;
    for (uint i = 0; i < dim; i++){
        for (uint j = 0; j < dim; j++){
            data[i][j] = mat->get(i,j);
        }
    }
}

void Matrix::copy(uint * _data, uint source_dim){
    if (dim > 0 && data != NULL && dim != source_dim){
        // matrix dimension different - release memory first
        for (uint i = 0; i < dim; i++){
            delete[] data[i];
        }
        delete[] data;
    }
    if (dim != source_dim){
        data = new uint*[source_dim];
        for (uint i = 0; i < source_dim; i++){
            data[i] = new uint[source_dim];
        }
    }
    dim = source_dim;
    for (uint i = 0; i < dim; i++){
        for (uint j = 0; j < dim; j++){
            data[i][j] = _data[i * source_dim + j];
        }
    }
}


bool Matrix::equal_to(Matrix * mat){
    uint source_dim = mat->get_dim();
    if  (dim != source_dim){
        return false;
    }
    for (uint i = 0; i < dim; i++){
        for (uint j = 0; j < dim; j++){
            if (data[i][j] != mat->get(i,j)){
                return false;
            }
        }
    }
    return true;
}


Matrix::~Matrix(){
    // cout << "releasing matrix memory" << endl;
    for (uint i = 0; i < dim; i++){
        delete[] data[i];
    }
    delete[] data;
}

uint Matrix::get(uint x, uint y){
    if (x >= dim || y >= dim){
        LOG("invalid get parameters (%u, %u) for a %u x %u matrix", x, y, dim, dim);
        return 0;
    }
    return data[x][y];
}

bool Matrix::set(uint val, uint x, uint y){
    if (x >= dim || y >= dim){
        LOG("invalid get parameters (%u, %u) for a %u x %u matrix", x, y, dim, dim);
        return false;
    }
    data[x][y] = val;
    return true;
}

bool Matrix::add(uint val, uint x, uint y){
    if (x >= dim || y >= dim){
        LOG("invalid get parameters (%u, %u) for a %u x %u matrix", x, y, dim, dim);
        return false;
    }
    data[x][y] += val;
    return true;
}

bool Matrix::subtract(uint val, uint x, uint y){
    if (x >= dim || y >= dim){
        LOG("invalid get parameters (%u, %u) for a %u x %u matrix", x, y, dim, dim);
        return false;
    }
    data[x][y] -= val;
    return true;
}

// 按比例压缩矩阵：每个元素变成 ceil(原值 / factor)，同时把 unit 放大 factor 倍。
//
// 数值和 unit 的乘积保持不变，因此矩阵表达的"总单位数"不变；但数值变小会
// 降低最大行/列和（max row/col sum），从而减少 Birkhoff 分解出的置换阶段数。
// 代价是每个元素都被向上取整，产生一定的量化误差。factor=1 时数值不变。
void Matrix::scale(uint factor){
    for (uint i = 0; i < dim; i++){
        for (uint j = 0; j < dim; j++){
            data[i][j] = (data[i][j] + factor - 1) / factor;   // 向上取整除法
        }
    }
    unit = unit * factor;
}

void Matrix::print(){
    printf("Print %u x %u matrix:\n", dim, dim);
    for(uint i = 0; i < dim; i ++){
        for (uint j = 0; j < dim; j++){
            cout << setw(10);
            cout << data[i][j];
        }
        cout << endl;
    }
    cout << endl << "is_sdsm: "<< (sdsm_info.is_sdsm ? "true" : "false") << ", max_row_col_sum: " << sdsm_info.max_row_col_sum << endl;
    cout << "non_max_row_idx: ";
    for (vector<struct row_col_info_t>::iterator row = sdsm_info.non_max_row.begin(); row != sdsm_info.non_max_row.end(); row++){
        cout << row->idx << " ";
    }
    cout << endl << "non_max_col_idx: ";
    for (vector<struct row_col_info_t>::iterator col = sdsm_info.non_max_col.begin(); col != sdsm_info.non_max_col.end(); col++){
        cout << col->idx << " ";
    }
    cout << endl;
}

// 扫描矩阵，收集行/列和，并计算 SDSM（缩放双随机矩阵）的元信息。
//
// 实现上分两轮：
//   1. 先遍历所有行，维护当前最大的行和 max_sum，并把小于它的行放进
//      non_max_row；如果后面出现更大的行和，之前记录的"最大行"也要
//      降级进 non_max_row。
//   2. 再遍历所有列做同样的事；注意列扫描可能出现更大的 max_sum，
//      此时第一轮记录的最大行也要一起降级。
// 最后用 same_sum_count 统计"和等于 max_sum"的行列数量，想要判断是否
// 全部行/列都达到了最大和。non_max_row/non_max_col 就是后续补齐
// 双随机矩阵时要"填充"的位置。
//
// 注意一个实现细节：第一个建立 max_sum 的行不会计入 same_sum_count
// （列方向同理），所以即使矩阵已经满足"所有行和 = 所有列和"，计数一般
// 也只有 2*dim-1，is_sdsm 仍然是 false。这不是注释写错，而是原实现的
// 计数偏差；实际流程中 to_scaled_doubly_stochastic_matrix() 总会走一遍
// 补齐分支（列表为空时是空操作），并在补完后把 is_sdsm 置为 true。
void Matrix::get_sdsm_info(){
    // reset sdsm info
    sdsm_info.is_sdsm = false;
    sdsm_info.non_max_row.clear();
    sdsm_info.non_max_col.clear();
    vector<struct row_col_info_t> max_row_idx;   // 当前行和中等于最大值的行
    vector<struct row_col_info_t> max_col_idx;   // 当前列和中等于最大值的列
    uint max_sum = 0, same_sum_count = 0;
    for (uint i = 0; i < dim; i++){
        uint row_sum = 0;
        for (uint j = 0; j < dim; j++){
            row_sum += data[i][j];
        }
        struct row_col_info_t row_temp = {.idx = i, .sum = row_sum};
        if (row_sum > max_sum){
            // 出现了更大的行和：之前认为"最大"的行其实都偏小，降级到 non_max_row
            max_sum = row_sum;
            std::copy(make_move_iterator(max_row_idx.begin()), make_move_iterator(max_row_idx.end()), back_inserter(sdsm_info.non_max_row));
            max_row_idx.clear();
            max_row_idx.push_back(row_temp);
        }else if (row_sum == max_sum){
            max_row_idx.push_back(row_temp);
            same_sum_count ++;
        }else{
            sdsm_info.non_max_row.push_back(row_temp);
        }
    }

    for (uint i = 0; i < dim; i++){
        uint col_sum = 0;
        for (uint j = 0; j < dim; j++){
            col_sum += data[j][i];
        }
        struct row_col_info_t col_temp = {.idx = i, .sum = col_sum};
        if (col_sum > max_sum){
            // 列方向也可能出现更大的和，此时两轮里记录的"最大行"同样要降级
            max_sum = col_sum;
            std::copy(make_move_iterator(max_col_idx.begin()), make_move_iterator(max_col_idx.end()), back_inserter(sdsm_info.non_max_col));
            max_col_idx.clear();
            max_col_idx.push_back(col_temp);
            std::copy(make_move_iterator(max_row_idx.begin()), make_move_iterator(max_row_idx.end()), back_inserter(sdsm_info.non_max_row));
            max_row_idx.clear();
        }else if (col_sum == max_sum){
            max_col_idx.push_back(col_temp);
            same_sum_count ++;
        }else{
            sdsm_info.non_max_col.push_back(col_temp);
        }
    }
    // 设计上，所有行、所有列的和都等于 max_sum 时才认为已经是 SDSM。
    // 但如上所述，首个达到 max_sum 的行/列没有被计数，因此这里对平衡
    // 矩阵也通常算出 false。真正的 SDSM 标志由补齐函数负责置 true。
    sdsm_info.is_sdsm = (same_sum_count == dim + dim);
    sdsm_info.max_row_col_sum = max_sum;
}
