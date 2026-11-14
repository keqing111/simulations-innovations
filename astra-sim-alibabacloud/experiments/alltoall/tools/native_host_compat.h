// Host scheduler sources use malloc only; do not require CUDA just for an
// otherwise unused cuda_runtime.h include. Never replace allocation/runtime APIs.
#include "fast_alltoall/alltoall_define.h"
#undef CUDA_NCCL_COMPILE
#define CUDA_NCCL_COMPILE 0
