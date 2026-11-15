# -*- coding: utf-8 -*-
"""Faithful NumPy/Python port of the FAST (MangoBoost) all-to-all scheduler.

The checked-in fixture suite cross-validates this module against the native
C++ simulator without requiring a machine-specific source path.

Semantics notes (details in NOTES_zh.md):
  * All integer work mirrors the C++ `uint` (= unsigned int) semantics;
    Python ints are unbounded, so no wraparound is needed for valid inputs.
  * The Kuhn matching iterates neighbors of each row in the iteration order
    of libstdc++ std::unordered_set<unsigned>; class LibstdcxxUSet replicates
    that order bit-for-bit (validated by differential tests).
  * Double arithmetic is accumulated in the same expression/loop order as the
    C++ code so that fp results match to the last bit in most cases.
"""

import numpy as np

# ---------------------------------------------------------------------------
# link parameter tables (scheduler/config.cpp:8-59)
# alpha: us ; beta: us/MB
# ---------------------------------------------------------------------------
INTRA_LINKS = {
    'DGX2':   (0.1, 8.0),
    'MI300X': (0.1, 5.0),
    'NDV2':   (0.1, 46.0),
    'FAST':   (0.1, 1.0),
    'H100':   (0.1, 2.3),
    'B100':   (0.1, 1.2),
}
INTER_LINKS = {
    'INFB':     (1.7, 106.0),
    'ETHER100': (0.7, 84.0),
    'ETHER400': (0.7, 22.0),
}
# type names as used by the C++ enums
INTER_TYPES = ['ETHER100', 'ETHER400', 'INFB']          # config.h:11-13
INTRA_TYPES = ['DGX2', 'MI300X', 'NDV2', 'FAST', 'H100', 'B100']  # config.h:7-9

SCALING_FACTOR_MIN = 1        # define.h:18
SCALING_FACTOR_MAX = 1000000  # define.h:19
SCALING_FACTOR_STEP = 2       # define.h:20
MAX_SUM_LIMIT = 100           # define.h:22


def get_intra_link_info(name):
    a, b = INTRA_LINKS[name]
    return {'alpha': a, 'beta': b}


def get_inter_link_info(name):
    a, b = INTER_LINKS[name]
    return {'alpha': a, 'beta': b}


# ---------------------------------------------------------------------------
# libstdc++ unordered_set<unsigned> iteration-order model
# (validated against g++ 11 by differential fuzzing; see NOTES_zh.md)
# ---------------------------------------------------------------------------
_BUCKET_LIST = [13, 29, 59, 127, 257, 541, 1109, 2357, 5087, 10273, 20773,
                41927, 84631, 170713, 344389, 694357, 1399049, 2818993,
                5677957, 11436019, 23029397, 46369009, 93342701, 187891021,
                378292831, 761648297, 1533326069, 3087278161, 6216615511,
                12517883333]


class LibstdcxxUSet(object):
    """Iteration order of std::unordered_set<unsigned> (libstdc++) for
    insert/erase sequences.  Inserting an existing key is a no-op (the C++
    code never rehashes or moves nodes for duplicate inserts)."""

    def __init__(self):
        self.B = 1
        self.nodes = []          # iteration order (keys)
        self._member = set()
        self.chains = {}         # bucket -> list of keys, chain-head first

    def _place(self, k):
        b = k % self.B
        if b in self.chains:
            first = self.chains[b][0]
            idx = self.nodes.index(first)
            self.nodes.insert(idx, k)
            self.chains[b].insert(0, k)
        else:
            self.nodes.insert(0, k)
            self.chains[b] = [k]
        self._member.add(k)

    def insert(self, k):
        if k in self._member:
            return
        n = len(self.nodes)
        if (n + 1 > self.B) or (self.B == 1 and n == 0):
            need = max(n + 1, 2 * self.B)
            for p in _BUCKET_LIST:
                if p >= need:
                    self.B = p
                    break
            old = self.nodes
            self.nodes = []
            self.chains = {}
            for kk in old:
                self._place(kk)
        self._place(k)

    def erase(self, k):
        if k not in self._member:
            return
        b = k % self.B
        self.nodes.remove(k)
        self.chains[b].remove(k)
        if not self.chains[b]:
            del self.chains[b]
        self._member.remove(k)

    def __iter__(self):
        return iter(self.nodes)

    def __contains__(self, k):
        return k in self._member

    def __len__(self):
        return len(self.nodes)


# ---------------------------------------------------------------------------
# Matrix utilities (algorithm/matrix.cpp)
# ---------------------------------------------------------------------------

def matrix_from_list(data, dim):
    """Matrix(uint* _data, uint _dim, unit=1) -- row-major flat data."""
    return np.array(data, dtype=np.int64).reshape(dim, dim).copy()


def scale_matrix_values(data, factor):
    """Matrix::scale (matrix.cpp:179-186): ceil(x/factor) per entry.
    (unit *= factor is tracked by the caller as `unit`.)"""
    return (data + factor - 1) // factor


def get_sdsm_info(data):
    """Matrix::get_sdsm_info (matrix.cpp:209-258) -- verbatim port, including
    the running-max/demotion bookkeeping and the 'same_sum_count == 2*dim'
    test.  NOTE: because the first strict maximum is never counted, is_sdsm
    can only be true for the all-zero matrix; nonzero inputs always take the
    conversion path in to_scaled_doubly_stochastic_matrix."""
    dim = data.shape[0]
    non_max_row = []      # list of [idx, sum]
    non_max_col = []
    max_row_idx = []      # rows currently achieving the running max
    max_col_idx = []
    max_sum = 0
    same_sum_count = 0
    for i in range(dim):
        row_sum = int(data[i].sum())
        if row_sum > max_sum:
            max_sum = row_sum
            non_max_row.extend(max_row_idx)
            max_row_idx = [[i, row_sum]]
        elif row_sum == max_sum:
            max_row_idx.append([i, row_sum])
            same_sum_count += 1
        else:
            non_max_row.append([i, row_sum])
    for i in range(dim):
        col_sum = int(data[:, i].sum())
        if col_sum > max_sum:
            max_sum = col_sum
            non_max_col.extend(max_col_idx)
            max_col_idx = [[i, col_sum]]
            non_max_row.extend(max_row_idx)   # rows are no longer max
            max_row_idx = []
        elif col_sum == max_sum:
            max_col_idx.append([i, col_sum])
            same_sum_count += 1
        else:
            non_max_col.append([i, col_sum])
    return {
        'is_sdsm': same_sum_count == dim + dim,
        'max_sum': max_sum,
        'non_max_row': non_max_row,
        'non_max_col': non_max_col,
    }


def to_scaled_doubly_stochastic_matrix(data, info, is_scaled=False):
    """FastAll2All::to_scaled_doubly_stochastic_matrix (all2all.cpp:26-58).
    `info` must be the sdsm_info of the input matrix (already collected);
    returns (sds_data, sds_info) -- the conversion adds diffs only at
    (non-max row, non-max col) cells and forces is_sdsm = true."""
    sds = data.copy()
    # the C++ code mutates the copies of row/col sums held in sdsm_info
    nrow = [[r[0], r[1]] for r in info['non_max_row']]
    ncol = [[c[0], c[1]] for c in info['non_max_col']]
    max_sum = info['max_sum']
    if not info['is_sdsm']:
        for row in nrow:
            for col in ncol:
                if col[1] == max_sum:
                    continue
                diff = max_sum - max(row[1], col[1])
                sds[row[0], col[0]] += diff
                row[1] += diff
                col[1] += diff
                if row[1] == max_sum:
                    break
    return sds, {'is_sdsm': True, 'max_sum': max_sum,
                 'non_max_row': [], 'non_max_col': []}


# ---------------------------------------------------------------------------
# Scaling (algorithm/all2all.cpp:175-257)
# ---------------------------------------------------------------------------

def scale_matrix_info(mat, factor):
    """FastAll2All::scale_matrix -- ceil-divide by factor, then compute
    max(row/col sum) of the SCALED matrix."""
    dim = mat.shape[0]
    buf = scale_matrix_values(mat, factor)
    max_sum = 0
    for i in range(dim):
        rs = int(buf[i].sum())
        cs = int(buf[:, i].sum())
        max_sum = max(max_sum, rs, cs)
    return max_sum, buf


def limit_max_sum(s_info, server_n, gpu_n, inter_link, intra_link, MBpu):
    return s_info <= MAX_SUM_LIMIT


def always_scale(s_info, server_n, gpu_n, inter_link, intra_link, MBpu):
    return s_info <= server_n


def balance_alpha_beta(s_info, server_n, gpu_n, inter_link, intra_link, MBpu,
                       factor):
    """all2all.cpp:208-219 -- note that beta uses max_sum*factor*MBpu."""
    if s_info <= server_n:
        return True
    alpha = (inter_link['alpha'] * server_n * server_n
             + intra_link['alpha'] * server_n * server_n * gpu_n)
    beta = inter_link['beta'] * s_info * factor * MBpu
    return alpha / beta < 0.1


def scaling_binary_search(fn, l, r, step, mat, gpu_n, inter_link, intra_link,
                          MBpu):
    """all2all.cpp:222-239 (recursive, exact integer arithmetic)."""
    if l > r:
        return -1
    ele_num = (r - l) // step + 1
    mid = l + (ele_num // 2) * step
    max_sum, _ = scale_matrix_info(mat, mid)
    if fn(max_sum, mat.shape[0], gpu_n, inter_link, intra_link, MBpu, mid):
        ret = scaling_binary_search(fn, l, mid - step, step, mat, gpu_n,
                                    inter_link, intra_link, MBpu)
        return mid if ret == -1 else ret
    return scaling_binary_search(fn, mid + step, r, step, mat, gpu_n,
                                 inter_link, intra_link, MBpu)


def to_scaled_matrix(mat, gpu_n, inter_link, intra_link, MBpu):
    """FastAll2All::to_scaled_matrix (all2all.cpp:242-257).
    Returns (S_mat, scaling_factor) or (None, 1) if no factor found."""
    ret = scaling_binary_search(balance_alpha_beta, SCALING_FACTOR_MIN,
                                SCALING_FACTOR_MAX, SCALING_FACTOR_STEP,
                                mat, gpu_n, inter_link, intra_link, MBpu)
    if ret == -1:
        return None, 1
    return scale_matrix_values(mat, ret), ret


# ---------------------------------------------------------------------------
# Birkhoff decomposition (algorithm/all2all.cpp:60-138)
# ---------------------------------------------------------------------------

class FastAll2All(object):
    """FastAll2All -- server-level decomposition core."""

    def __init__(self, mat, gpu_n, inter_type, intra_type, MBpu=1.0):
        # Matrix copy (matrix.cpp:68-95)
        self.mat = np.array(mat, dtype=np.int64).reshape(mat.shape).copy()
        dim = self.mat.shape[0]
        self.is_scaled = False
        self.scaling_factor = 1
        self.MB_per_unit = MBpu
        self.inter_link = get_inter_link_info(inter_type)
        self.intra_link = get_intra_link_info(intra_type)
        self.gpu_n = gpu_n
        self.matching = [-1] * (dim * 2)
        self.visit = [False] * (dim * 2)
        self.row_to_col = [LibstdcxxUSet() for _ in range(dim)]
        self.S_mat = None
        self.SDS_mat = None
        self.sds_info = None
        self.p_sets = []      # list of {'mp': [row->col...], 'freq': uint}
        self.dim = dim

    # -- scaling ------------------------------------------------------------
    def to_scaled_matrix(self):
        S, factor = to_scaled_matrix(self.mat, self.gpu_n, self.inter_link,
                                     self.intra_link, self.MB_per_unit)
        if S is not None:
            self.S_mat = S
            self.scaling_factor = factor
            self.is_scaled = True

    # -- doubly stochastic conversion ---------------------------------------
    def to_scaled_doubly_stochastic_matrix(self):
        if not self.is_scaled:
            info = get_sdsm_info(self.mat)
            sds, sds_info = to_scaled_doubly_stochastic_matrix(
                self.mat, info, False)
        else:
            info = get_sdsm_info(self.S_mat)
            sds, sds_info = to_scaled_doubly_stochastic_matrix(
                self.S_mat, info, True)
        self.SDS_mat = sds
        self.sds_info = sds_info

    # -- greedy (Kuhn) perfect matching on the support graph ---------------
    def update_edges(self):
        """all2all.cpp:104-117 -- col vertex ids are j+dim; iteration order of
        every row adjacency set is the libstdc++ unordered_set order."""
        dim = self.dim
        sds = self.SDS_mat
        for i in range(dim):
            st = self.row_to_col[i]
            for j in range(dim):
                col_id = j + dim
                if sds[i, j] > 0:
                    st.insert(col_id)
                elif sds[i, j] == 0:
                    st.erase(col_id)

    def hungarian_dfs(self, u):
        """all2all.cpp:90-102 -- recursion order = neighbor set order."""
        dim = self.dim
        for col_idx in self.row_to_col[u]:
            if not self.visit[col_idx]:
                self.visit[col_idx] = True
                m = self.matching[col_idx]
                if m == -1 or self.hungarian_dfs(m):
                    self.matching[col_idx] = u
                    self.matching[u] = col_idx
                    return True
        return False

    def hungarian(self):
        """all2all.cpp:75-87."""
        dim = self.dim
        match_num = 0
        self.matching = [-1] * (dim * 2)
        for u in range(dim):
            if self.matching[u] == -1:
                self.visit = [False] * (dim * 2)
                if self.hungarian_dfs(u):
                    match_num += 1
        return match_num

    def update_permutation_sets(self):
        """all2all.cpp:119-138."""
        dim = self.dim
        sds = self.SDS_mat
        # matching[0] is a col id (dim..2dim-1) when matched
        min_freq = int(sds[0, self.matching[0] - dim])
        mp = [0] * dim
        for i in range(dim):
            col_id = self.matching[i] - dim
            min_freq = min(int(sds[i, col_id]), min_freq)
            mp[i] = col_id
        for i in range(dim):
            col_id = self.matching[i] - dim
            sds[i, col_id] -= min_freq
        freq = min_freq * self.scaling_factor   # PermutationSet::get_freq()
        self.p_sets.append({'mp': mp, 'freq': freq})
        return min_freq

    def decompose(self):
        """all2all.cpp:60-72."""
        if self.SDS_mat is None:
            raise RuntimeError('must convert matrix to sdsm first')
        self.p_sets = []
        freq_sum = 0
        max_sum = self.sds_info['max_sum']
        while freq_sum < max_sum:
            self.update_edges()
            match_num = self.hungarian()
            if match_num != self.dim:
                raise RuntimeError(
                    'hungarian did not find a perfect matching '
                    '(freq_sum=%d max_sum=%d match=%d)' %
                    (freq_sum, max_sum, match_num))
            freq_sum += self.update_permutation_sets()
        return self.p_sets

    # -- validation helpers -------------------------------------------------
    def verify_decomposition(self):
        """all2all.cpp:146-158 semantics (needs uint-exact arithmetic)."""
        dim = self.dim
        r = np.zeros((dim, dim), dtype=np.int64)
        for ps in self.p_sets:
            for i in range(dim):
                r[i, ps['mp'][i]] += ps['freq'] // self.scaling_factor
        self.to_scaled_doubly_stochastic_matrix()
        return np.array_equal(self.SDS_mat, r)


# ---------------------------------------------------------------------------
# topology-aware transfer cost models (scheduler/config.cpp)
# ---------------------------------------------------------------------------

def spread_out(data, dim, link, MBpu=1.0):
    """config.cpp:61-74 -- diagonal-max ring cost model."""
    r = 0.0
    for i in range(dim):
        largest = 0
        for j in range(dim):
            largest = max(largest, data[j][(j + i) % dim])
        if largest != 0:
            r += link['alpha'] + largest * link['beta'] * MBpu
    return r


def intra_transfer_full_mesh(data, dim, link, MBpu=1.0):
    """config.cpp:76-84."""
    max_ele = 0
    for i in range(dim):
        for j in range(dim):
            max_ele = max(max_ele, data[i][j])
    return link['alpha'] + max_ele * link['beta'] * MBpu


def intra_transfer_2ring(data, dim, link, MBpu=1.0):
    """config.cpp:152-179."""
    first_ring = 0.0
    for i in range(1, 4):
        largest = 0
        for j in range(dim):
            largest = max(largest, data[j][(j + i) % dim])
        if largest != 0:
            first_ring += (link['alpha'] + largest * link['beta'] * MBpu) * i
    reverse_ring = 0.0
    for i in range(1, 5):
        largest = 0
        for j in range(dim):
            largest = max(largest, data[j][(j + dim - i) % dim])
        if largest != 0:
            reverse_ring += (link['alpha'] + largest * link['beta'] * MBpu) * i
    return max(first_ring, reverse_ring)


def intra_transfer_hybrid_cube_mesh(data, dim, link, MBpu=1.0):
    """config.cpp:86-149 -- hardcoded 8-GPU layout; only valid when dim == 8.
    Verbatim port of the 32 pairwise costs and the 8 two-hop sums."""
    d = data
    r = [0.0] * 32
    r[0] = link['alpha'] + d[0][1] * link['beta'] / 2 * MBpu
    r[1] = link['alpha'] + d[0][2] * link['beta'] / 2 * MBpu
    r[2] = link['alpha'] + d[0][3] * link['beta'] * MBpu
    r[3] = link['alpha'] + d[1][0] * link['beta'] / 2 * MBpu
    r[4] = link['alpha'] + d[1][2] * link['beta'] * MBpu
    r[5] = link['alpha'] + d[1][3] * link['beta'] * MBpu
    r[6] = link['alpha'] + d[2][0] * link['beta'] / 2 * MBpu
    r[7] = link['alpha'] + d[2][1] * link['beta'] * MBpu
    r[8] = link['alpha'] + d[2][3] * link['beta'] / 2 * MBpu
    r[9] = link['alpha'] + d[3][0] * link['beta'] * MBpu
    r[10] = link['alpha'] + d[3][1] * link['beta'] * MBpu
    r[11] = link['alpha'] + d[3][2] * link['beta'] / 2 * MBpu
    r[12] = link['alpha'] + d[0][5] * link['beta'] * MBpu
    r[13] = link['alpha'] + d[1][4] * link['beta'] / 2 * MBpu
    r[14] = link['alpha'] + d[2][7] * link['beta'] * MBpu
    r[15] = link['alpha'] + d[3][6] * link['beta'] / 2 * MBpu
    r[16] = link['alpha'] + d[5][0] * link['beta'] * MBpu
    r[17] = link['alpha'] + d[4][1] * link['beta'] / 2 * MBpu
    r[18] = link['alpha'] + d[7][2] * link['beta'] * MBpu
    r[19] = link['alpha'] + d[6][3] * link['beta'] / 2 * MBpu
    r[20] = link['alpha'] + d[4][5] * link['beta'] / 2 * MBpu
    r[21] = link['alpha'] + d[4][6] * link['beta'] * MBpu
    r[22] = link['alpha'] + d[4][7] * link['beta'] * MBpu
    r[23] = link['alpha'] + d[5][4] * link['beta'] / 2 * MBpu
    r[24] = link['alpha'] + d[5][6] * link['beta'] * MBpu
    r[25] = link['alpha'] + d[5][7] * link['beta'] / 2 * MBpu
    r[26] = link['alpha'] + d[6][4] * link['beta'] * MBpu
    r[27] = link['alpha'] + d[6][5] * link['beta'] * MBpu
    r[28] = link['alpha'] + d[6][7] * link['beta'] / 2 * MBpu
    r[29] = link['alpha'] + d[7][4] * link['beta'] * MBpu
    r[30] = link['alpha'] + d[7][5] * link['beta'] / 2 * MBpu
    r[31] = link['alpha'] + d[7][6] * link['beta'] / 2 * MBpu
    result = max(r)
    # NOTE: x/2 inside MAX() is C++ INTEGER division (uint / uint literal);
    # the trailing (..)*beta/2 term is float division (uint*double already).
    t = [0.0] * 8
    t[0] = 2 * link['alpha'] + max(max(d[0][4] // 2, d[2][4]), d[3][4]) * link['beta'] * MBpu + (d[0][4] + d[2][4] + d[3][4]) * link['beta'] / 2 * MBpu
    t[1] = 2 * link['alpha'] + max(max(d[1][5] // 2, d[2][5] // 2), d[3][5]) * link['beta'] * MBpu + (d[1][5] + d[2][5] + d[3][5]) * link['beta'] * MBpu
    t[2] = 2 * link['alpha'] + max(max(d[0][6], d[1][6]), d[2][6] // 2) * link['beta'] * MBpu + (d[0][6] + d[1][6] + d[2][6]) * link['beta'] / 2 * MBpu
    t[3] = 2 * link['alpha'] + max(max(d[0][7] // 2, d[1][7]), d[3][7] // 2) * link['beta'] * MBpu + (d[0][7] + d[1][7] + d[3][7]) * link['beta'] * MBpu
    t[4] = 2 * link['alpha'] + max(max(d[4][0] // 2, d[6][0]), d[7][0] // 2) * link['beta'] * MBpu + (d[4][0] + d[6][0] + d[7][0]) * link['beta'] * MBpu
    t[5] = 2 * link['alpha'] + max(max(d[5][1] // 2, d[6][1]), d[7][1]) * link['beta'] * MBpu + (d[5][1] + d[6][1] + d[7][1]) * link['beta'] / 2 * MBpu
    t[6] = 2 * link['alpha'] + max(max(d[4][2], d[5][2] // 2), d[6][2] // 2) * link['beta'] * MBpu + (d[4][2] + d[5][2] + d[6][2]) * link['beta'] * MBpu
    t[7] = 2 * link['alpha'] + max(max(d[4][3], d[5][3]), d[7][3] // 2) * link['beta'] * MBpu + (d[4][3] + d[5][3] + d[7][3]) * link['beta'] / 2 * MBpu
    result += max(t)
    return result


def spread_out_baseline(data, server_n, gpu_n, inter_link, intra_link,
                        MBpu=1.0):
    """config.cpp:182-208 -- spread-out baseline over the gpu-level matrix."""
    r = 0.0
    dim = server_n * gpu_n
    for i in range(dim):
        largest_sending = 0.0
        for j in range(dim):
            row_id = j
            col_id = (j + i) % dim
            if data[row_id][col_id] != 0:
                if row_id // gpu_n == col_id // gpu_n:
                    current = intra_link['alpha'] + data[row_id][col_id] * intra_link['beta'] * MBpu
                else:
                    current = inter_link['alpha'] + data[row_id][col_id] * inter_link['beta'] * MBpu
                largest_sending = max(largest_sending, current)
        r += largest_sending
    return r


# ---------------------------------------------------------------------------
# LocalScheduler (scheduler/local.cpp)
# ---------------------------------------------------------------------------

class LocalScheduler(object):
    """Intra-server scheduler. data: gpu_n x (gpu_n*server_n) row-major copy of
    the rows of the gpu-level matrix that belong to this server."""

    def __init__(self, data, gpu_n, server_n, server_id, intra_link):
        self.gpu_n = gpu_n
        self.server_n = server_n
        self.server_id = server_id
        self.dim = gpu_n * server_n
        self.data = np.array(data, dtype=np.int64).reshape(gpu_n,
                                                           gpu_n * server_n).copy()
        self.balanced_data = self.data.copy()
        self.intra_link = intra_link
        self.server2server_data = [0] * server_n
        self.row_sum = [0] * (gpu_n * server_n)
        self.intrinsic_all2all = np.zeros((gpu_n, gpu_n), dtype=np.int64)

    # local.cpp:148-178
    def prepare_load_balance(self):
        gpu_n, server_n = self.gpu_n, self.server_n
        self.row_sum = [0] * (gpu_n * server_n)
        for i in range(server_n):
            if i == self.server_id:
                self.server2server_data[i] = 0
                for j in range(gpu_n):
                    for k in range(gpu_n):
                        self.intrinsic_all2all[j, k] = \
                            self.data[j][self.server_id * gpu_n + k]
                continue
            for j in range(gpu_n):
                self.row_sum[i * gpu_n + j] = 0
                for k in range(gpu_n):
                    self.row_sum[i * gpu_n + j] += \
                        self.data[j][i * gpu_n + k]
            row_avg = sum(self.row_sum[i * gpu_n + k] for k in range(gpu_n))
            self.server2server_data[i] = (row_avg + gpu_n - 1) // gpu_n

    # local.cpp:188-217
    def check_balance_dispatch(self, to_server_id, MBpu=1.0):
        gpu_n = self.gpu_n
        max_row = 0
        for k in range(gpu_n):
            max_row = max(self.row_sum[to_server_id * gpu_n + k], max_row)
        alpha_cost = self.intra_link['alpha']
        beta_cost = max_row * self.intra_link['beta'] * MBpu
        for i in range(gpu_n):
            largest = 0
            for j in range(gpu_n):
                largest = max(largest, self.balanced_data[j][
                    to_server_id * gpu_n + ((j + i) % gpu_n)])
            if largest != 0:
                alpha_cost += self.intra_link['alpha']
                beta_cost += largest * self.intra_link['beta'] * MBpu
        return (beta_cost / alpha_cost) < 2

    # local.cpp:180-186
    def server2server_approx(self, to_server_id):
        gpu_n = self.gpu_n
        max_row = 0
        for k in range(gpu_n):
            max_row = max(self.row_sum[to_server_id * gpu_n + k], max_row)
        self.server2server_data[to_server_id] = max_row

    # local.cpp:309-352 -- balance to server2server_data[] targets (used by
    # pipeline2/pipeline3)
    def server2server_balance(self, to_server_id, balance):
        gpu_n = self.gpu_n
        target = self.server2server_data[to_server_id]
        smaller_row = []
        bigger_row = []
        for i in range(gpu_n):
            rs = self.row_sum[to_server_id * gpu_n + i]
            if rs < target:
                smaller_row.append(i)
            elif rs > target:
                bigger_row.append(i)
        for big_row in bigger_row:
            rm_data = self.row_sum[to_server_id * gpu_n + big_row] - target
            small_iter = 0
            while small_iter < len(smaller_row):
                small_row = smaller_row[small_iter]
                for j in range(gpu_n):
                    mv_data = min(rm_data,
                                  self.balanced_data[big_row][
                                      to_server_id * gpu_n + j],
                                  target - self.row_sum[
                                      to_server_id * gpu_n + small_row])
                    rm_data -= mv_data
                    self.row_sum[to_server_id * gpu_n + small_row] += mv_data
                    self.row_sum[to_server_id * gpu_n + big_row] -= mv_data
                    self.balanced_data[big_row][to_server_id * gpu_n + j] -= mv_data
                    self.balanced_data[small_row][to_server_id * gpu_n + j] += mv_data
                    balance[big_row][small_row] += mv_data
                if rm_data == 0:
                    break
                if self.row_sum[to_server_id * gpu_n + small_row] == target:
                    del smaller_row[small_iter]
                else:
                    small_iter += 1
            if not smaller_row:
                break

    # local.cpp:220-283 -- balance to a fixed freq target (used by pipeline1)
    def server2server_balance_freq(self, to_server_id, balance, freq):
        gpu_n = self.gpu_n
        smaller_row = []
        bigger_row = []
        for i in range(gpu_n):
            rs = self.row_sum[to_server_id * gpu_n + i]
            if rs < freq:
                smaller_row.append(i)
            elif rs > freq:
                bigger_row.append(i)
        for small_row in smaller_row:
            offset = freq - self.row_sum[to_server_id * gpu_n + small_row]
            big_iter = 0
            while big_iter < len(bigger_row):
                big_row = bigger_row[big_iter]
                for j in range(gpu_n):
                    move_data = min(
                        self.row_sum[to_server_id * gpu_n + big_row] - freq,
                        offset,
                        self.balanced_data[big_row][to_server_id * gpu_n + j])
                    offset -= move_data
                    self.row_sum[to_server_id * gpu_n + big_row] -= move_data
                    self.row_sum[to_server_id * gpu_n + small_row] += move_data
                    self.balanced_data[big_row][to_server_id * gpu_n + j] -= move_data
                    self.balanced_data[small_row][to_server_id * gpu_n + j] += move_data
                    balance[big_row][small_row] += move_data
                if offset == 0:
                    break
                if self.row_sum[to_server_id * gpu_n + big_row] == freq:
                    del bigger_row[big_iter]
                else:
                    big_iter += 1
            if not bigger_row:
                break
        # second phase: row transfer accounting (dispatch bookkeeping)
        for i in range(gpu_n):
            row_transfer = 0
            for j in range(gpu_n):
                transfer = min(freq - row_transfer,
                               self.balanced_data[i][to_server_id * gpu_n + j])
                row_transfer += transfer
                self.balanced_data[i][to_server_id * gpu_n + j] -= transfer
                self.row_sum[to_server_id * gpu_n + i] -= transfer
                if i != j:
                    pass  # balance matrix has no dispatch field here
                if row_transfer == freq:
                    break

    # local.cpp:285-306
    def server2server_dispatch(self, to_server_id, dispatch, freq):
        gpu_n = self.gpu_n
        transferred = []
        for i in range(gpu_n):
            row_transfer = 0
            for j in range(gpu_n):
                transfer = min(freq - row_transfer,
                               self.balanced_data[i][to_server_id * gpu_n + j])
                row_transfer += transfer
                self.balanced_data[i][to_server_id * gpu_n + j] -= transfer
                self.row_sum[to_server_id * gpu_n + i] -= transfer
                if i != j:
                    dispatch[i][j] += transfer
                if row_transfer == freq:
                    break
            transferred.append(row_transfer)
        return transferred


# ---------------------------------------------------------------------------
# GlobalScheduler: server-level matrix build + pipelined cost model
# (scheduler/global.cpp)
# ---------------------------------------------------------------------------

class GlobalScheduler(object):
    def __init__(self, server_n, gpu_n, locals_, inter_type, intra_type,
                 scaling=False, MBpu=1.0, inter_override=None,
                 intra_override=None, decomposition=None):
        self.server_n = server_n
        self.gpu_n = gpu_n
        self.locals = locals_
        self.enable_scaling = scaling
        self.decomposition = decomposition
        self.inter_info = inter_override or get_inter_link_info(inter_type)
        self.intra_info = intra_override or get_intra_link_info(intra_type)
        n = len(locals_) * server_n
        self.if_approx = [False] * n
        data = np.zeros((server_n, server_n), dtype=np.int64)
        for local in locals_:
            local.prepare_load_balance()
            src = local.server_id
            for j in range(server_n):
                approx = local.check_balance_dispatch(j, MBpu)
                self.if_approx[src * server_n + j] = approx
                if approx:
                    local.server2server_approx(j)
                data[src, j] = local.server2server_data[j]
        self.mat = data

    def permutation_set_cost(self, freq, MBpu=1.0):
        """global.cpp:532-534."""
        return self.inter_info['alpha'] + freq * self.inter_info['beta'] * MBpu

    # -- pipeline2 (global.cpp:238-403): intra cost = spread_out -----------
    def pipeline2(self, MBpu=1.0):
        # C++ signature: pipeline2(double MBpu, scaling_success_condition fn);
        # the driver always passes &balance_alpha_beta.  Scaling happens only
        # when enable_scaling was set in the ctor (global.cpp:270-272).
        return self._pipeline(MBpu, 'spread_out', None,
                              scale=self.enable_scaling)

    # -- pipeline3 (global.cpp:73-235): intra cost = topology fn ------------
    def pipeline3(self, MBpu=1.0, intra_fn='full_mesh'):
        # NOTE: pipeline3 NEVER scales the server matrix, even when
        # enable_scaling is set (global.cpp:74-77 has no to_scaled_matrix).
        return self._pipeline(MBpu, 'fn', intra_fn, scale=False)

    def _pipeline(self, MBpu, intra_mode, intra_fn, scale):
        server_n, gpu_n = self.server_n, self.gpu_n
        locals_ = self.locals

        # FastAll2All on the server-level matrix (global.cpp:75 / 269)
        app = FastAll2All(self.mat, gpu_n, 'INFB', 'DGX2', MBpu)
        # inter/intra types matter for the balance_alpha_beta scaling
        # decision; everything else is link-type agnostic in the app.
        app.inter_link = self.inter_info
        app.intra_link = self.intra_info
        app.MB_per_unit = MBpu
        if scale:
            app.to_scaled_matrix()
        app.to_scaled_doubly_stochastic_matrix()
        if self.decomposition is None:
            app.decompose()
        p_sets = app.p_sets

        def intra_cost(mat, link):
            if intra_mode == 'spread_out':
                return spread_out(mat, gpu_n, link, MBpu)
            if intra_fn == 'full_mesh':
                return intra_transfer_full_mesh(mat, gpu_n, link, MBpu)
            if intra_fn == '2ring':
                return intra_transfer_2ring(mat, gpu_n, link, MBpu)
            return intra_transfer_hybrid_cube_mesh(mat, gpu_n, link, MBpu)

        pset_sz = len(p_sets)
        locals_sz = len(locals_)

        # balance buffers (one per local/dst pair)
        bs = {}
        for lid in range(locals_sz):
            for s in range(server_n):
                bs[(lid, s)] = np.zeros((gpu_n, gpu_n), dtype=np.int64)
        # balance once per (local, dst) if the approx heuristic says so
        for lid in range(locals_sz):
            for s in range(locals_sz):
                if s == locals_[lid].server_id:
                    continue
                if not self.if_approx[lid * server_n + s]:
                    locals_[lid].server2server_balance(s, bs[(lid, s)])

        if self.decomposition is not None:
            # Opt-in policies need exact rows AFTER the native approximation /
            # balancing decision; aggregate/gpu_n is wrong when balance is skipped.
            app.real_gpu_remaining = np.zeros((server_n,gpu_n,server_n),dtype=np.int64)
            app.real_gpu_contents = np.concatenate([local.balanced_data for local in locals_],axis=0).copy()
            for src,local in enumerate(locals_):
                for dst in range(server_n):
                    if src != dst:
                        app.real_gpu_remaining[src,:,dst] = local.balanced_data[:,dst*gpu_n:(dst+1)*gpu_n].sum(axis=1)
            app.p_sets = self.decomposition(app)
            p_sets = app.p_sets
            pset_sz = len(p_sets)

        # dispatch buffers per (pset, local)
        ds = {}
        for pid in range(pset_sz):
            for lid in range(locals_sz):
                ds[(pid, lid)] = np.zeros((gpu_n, gpu_n), dtype=np.int64)

        # keep artifacts for cross-validation / inspection
        self.last_app = app
        self.last_bs = bs
        self.last_ds = ds
        self.last_inter_rows = {}
        self.last_intra_mode = intra_mode
        self.last_intra_fn = intra_fn

        for pid, pset in enumerate(p_sets):
            for lid, local in enumerate(locals_):
                src = local.server_id
                dst = pset['mp'][src]
                transferred = local.server2server_dispatch(
                    dst, ds[(pid, lid)], pset['freq']
                )
                self.last_inter_rows[(pid, lid)] = (
                    transferred if src != dst else [0] * gpu_n
                )

        # ---- runtime model (global.cpp:321-402) ----
        t = 0.0
        bound = 0.0
        # balance time
        largest_intra_time = 0.0
        for i in range(locals_sz):
            balance_time = 0.0
            for s in range(locals_sz):
                if s == locals_[i].server_id:
                    continue
                balance_time += intra_cost(bs[(i, s)], locals_[i].intra_link)
            largest_intra_time = max(largest_intra_time, balance_time)
        t += largest_intra_time

        # first inter transfer overlaps the intrinsic all2all
        inter = self.permutation_set_cost(p_sets[0]['freq'], MBpu)
        largest_intra_time = 0.0
        for i in range(locals_sz):
            intra1 = intra_cost(locals_[i].intrinsic_all2all,
                                locals_[i].intra_link)
            largest_intra_time = max(largest_intra_time, intra1)
        t += max(inter, largest_intra_time)
        bound += inter

        # pipeline the middle psets: prev dispatch overlaps current inter
        for cur_pid in range(1, pset_sz):
            prev_pid = cur_pid - 1
            largest_intra_time = 0.0
            for i in range(locals_sz):
                intra1 = intra_cost(ds[(prev_pid, i)], locals_[i].intra_link)
                largest_intra_time = max(largest_intra_time, intra1)
            inter = self.permutation_set_cost(p_sets[cur_pid]['freq'], MBpu)
            t += max(largest_intra_time, inter)
            bound += inter

        # final dispatch
        largest_intra_time = 0.0
        for i in range(locals_sz):
            intra1 = intra_cost(ds[(pset_sz - 1, i)], locals_[i].intra_link)
            largest_intra_time = max(largest_intra_time, intra1)
        t += largest_intra_time

        ratio = (t - bound) / t
        return {'t': t, 'ratio': ratio, 'bound': bound, 'p_sets': p_sets}


# ---------------------------------------------------------------------------
# end-to-end runner mimicking FastAll2AllTester::run (test.cpp:73-116)
# ---------------------------------------------------------------------------

def run_fast_full(gpu_matrix, server_n, gpu_n, inter_type, intra_type,
                  scaling=False, MBpu=1.0, pipeline='p2', intra_fn='full_mesh',
                  zero_diag=True, inter_override=None, intra_override=None,
                  decomposition=None):
    """gpu_matrix: dim x dim numpy int matrix (dim = server_n*gpu_n).
    Returns a dict with the full pipeline result, mirroring the C++ tester.
    inter_override/intra_override: optional {'alpha':us,'beta':us/MB} dicts
    replacing the class lookups (for bandwidth-ratio sweeps)."""
    dim = server_n * gpu_n
    mat = np.array(gpu_matrix, dtype=np.int64).copy()
    if zero_diag:
        for i in range(dim):
            mat[i, i] = 0
    intra_info = intra_override or get_intra_link_info(intra_type)
    inter_info = inter_override or get_inter_link_info(inter_type)
    locals_ = []
    for s in range(server_n):
        loc = LocalScheduler(mat[s * gpu_n:(s + 1) * gpu_n, :], gpu_n,
                             server_n, s, intra_info)
        locals_.append(loc)
    gs = GlobalScheduler(server_n, gpu_n, locals_, inter_type, intra_type,
                         scaling, MBpu, inter_override, intra_override, decomposition)
    if pipeline == 'p2':
        r = gs.pipeline2(MBpu)
    else:
        r = gs.pipeline3(MBpu, intra_fn)
    baseline = spread_out_baseline(mat, server_n, gpu_n, inter_info,
                                   intra_info, MBpu)
    return {
        't': r['t'], 'ratio': r['ratio'], 'bound': r['bound'],
        'baseline': baseline, 'speedup': baseline / r['t'],
        'p_sets': r['p_sets'], 'mat': mat,
        'if_approx': gs.if_approx, 'gs': gs, 'locals': locals_,
        'server_mat': gs.mat,
    }


# ---------------------------------------------------------------------------
# schedule emission: turn the C++ runtime model's timeline into concrete
# (src, dst, bytes, start_us) flows, for injection into a packet-level sim
# ---------------------------------------------------------------------------

def emit_schedule(r, MBpu=1e-6):
    """Translate a run_fast_full() result into the physical flow schedule the
    C++ runtime model implies (global.cpp:321-402).

    Returns (flows, meta): flows = list of (src, dst, bytes, start_us);
    meta = timeline dict.  This is the port's interpretation of the model's
    stage structure (the C++ tool models costs per stage, not individual
    flows; the per-flow split below is deterministic and byte-conserving):

      * balance (prep): t=0; within each server the dst-servers are drained
        in ascending order; every nonzero bs cell is one intra flow.
      * inter stage k: GPU i of server s sends the real bytes consumed from
        its balanced row to GPU i of p_sets[k]['mp'][s].  The server matrix
        and permutation frequency are per-GPU quantities, so these recorded
        row transfers must not be divided by gpu_n.  SDSM virtual completion
        cells consume zero real bytes.
      * grey tiles: each server's intrinsic intra a2a, starts together with
        inter stage 0 (the model takes the max of the two).
      * dispatch k: intra redistribution for stage k, starts at the beginning
        of inter stage k+1; the last dispatch runs after the last inter.
    """
    gs = r['gs']
    gpu_n, server_n = gs.gpu_n, gs.server_n
    locals_ = r['locals']
    p_sets = r['p_sets']
    inter_info, intra_info = gs.inter_info, gs.intra_info
    intra_mode = getattr(gs, 'last_intra_mode', 'spread_out')
    intra_fn = getattr(gs, 'last_intra_fn', 'full_mesh')

    if intra_mode == 'fn':
        if intra_fn == 'full_mesh':
            def intra_cost(mat, link):
                return intra_transfer_full_mesh(mat, gpu_n, link, MBpu)
        elif intra_fn == '2ring':
            def intra_cost(mat, link):
                return intra_transfer_2ring(mat, gpu_n, link, MBpu)
        else:
            def intra_cost(mat, link):
                return intra_transfer_hybrid_cube_mesh(mat, gpu_n, link, MBpu)
    else:
        def intra_cost(mat, link):
            return spread_out(mat, gpu_n, link, MBpu)

    def inter_cost(freq):
        return inter_info['alpha'] + freq * inter_info['beta'] * MBpu

    flows = []
    flow_metadata = []

    def append_flow(src, dst, size, start_us, phase, stage_id):
        flows.append((src, dst, int(size), start_us))
        flow_metadata.append({'phase': phase, 'stage_id': int(stage_id)})

    bs = gs.last_bs

    # -- balance phase ------------------------------------------------------
    bal_end = []
    for lid, local in enumerate(locals_):
        t = 0.0
        for s in range(server_n):
            if s == local.server_id:
                continue
            B = bs[(lid, s)]
            for i in range(gpu_n):
                for j in range(gpu_n):
                    if B[i, j] > 0:
                        append_flow(lid * gpu_n + i, lid * gpu_n + j,
                                    B[i, j], t, 'balance', s)
            t += intra_cost(B, intra_info)
        bal_end.append(t)
    t_bal = max(bal_end)

    # -- inter stages + overlapping dispatch (replay holdings) ---------------
    K = len(p_sets)
    t = t_bal
    starts = []
    slot_ends = []
    for k in range(K):
        starts.append(t)
        pset = p_sets[k]
        freq = pset['freq']
        for lid, local in enumerate(locals_):
            d = pset['mp'][local.server_id]
            base_s, base_d = lid * gpu_n, d * gpu_n
            for i, amount in enumerate(gs.last_inter_rows[(k, lid)]):
                if amount > 0:
                    append_flow(
                        base_s + i, base_d + i, amount, t, 'inter', k
                    )
        if k == 0:
            grey = [intra_cost(l.intrinsic_all2all, intra_info)
                    for l in locals_]
            inter = pset.get('inter_duration_ns', None)
            inter = inter / 1000.0 if inter is not None else inter_cost(pset['freq'])
            slot = max(inter, max(grey))
            for lid, local in enumerate(locals_):
                G = local.intrinsic_all2all
                for i in range(gpu_n):
                    for j in range(gpu_n):
                        if i != j and G[i, j] > 0:
                            append_flow(lid * gpu_n + i, lid * gpu_n + j,
                                        G[i, j], t, 'intrinsic', 0)
        else:
            disp = [intra_cost(gs.last_ds[(k - 1, lid)], intra_info)
                    for lid in range(server_n)]
            inter = pset.get('inter_duration_ns', None)
            inter = inter / 1000.0 if inter is not None else inter_cost(pset['freq'])
            slot = max(max(disp), inter)
        t += slot
        slot_ends.append(t)
        # dispatch k flows start at the next slot boundary
        for lid, local in enumerate(locals_):
            D = gs.last_ds[(k, lid)]
            destination = pset['mp'][lid]
            # ds is indexed by original sender, but redistribution happens at
            # the receiving server. Self matches contain only padding and must
            # not replay intrinsic traffic as an additional dispatch.
            if destination == lid:
                continue
            for i in range(gpu_n):
                for j in range(gpu_n):
                    if D[i, j] > 0:
                        append_flow(destination * gpu_n + i, destination * gpu_n + j,
                                    D[i, j], t, 'dispatch', k)

    return flows, {
        't_us': r['t'], 'baseline_us': r['baseline'],
        'speedup': r['speedup'], 'bound_us': r['bound'],
        't_bal_us': t_bal, 'stage_starts_us': starts,
        'slot_ends_us': slot_ends,
        'final_dispatch_start_us': t if K else t_bal,
        'n_stages': K, 'n_flows': len(flows),
        'flow_metadata': flow_metadata,
    }
