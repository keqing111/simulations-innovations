# ============================================================================
# planning/validation/test_figures.py：最终出图的两个非图形约束。
#
#   * CPU 诊断登记的二进制必须与 bridge 的最终二进制一致；
#     不一致（例如混入 V1 计时）必须抛 ValueError；
#   * gm() 是几何平均，[0.5,2] 的中性比值应等于 1。
# 这些约束保证图上的 CPU 数字不会与语义验证的二进制版本脱节。
# ============================================================================

import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('report_figures',Path(__file__).with_name('report_figures.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)


class FigureTests(unittest.TestCase):
    def test_final_cpu_provenance_and_geometric_mean(self):
        # 二进制一致 → 允许；不一致 → 拒绝。
        f.require_final_binary({'binary_sha256':'final'},{'identity':{'binary_sha256':'final'}})
        with self.assertRaises(ValueError):f.require_final_binary({'binary_sha256':'final'},{'identity':{'binary_sha256':'v1'}})
        # 几何平均：0.5 与 2 的乘积为 1，GM 应为 1。
        self.assertEqual(f.gm([.5,2]),1)


if __name__=='__main__':unittest.main()
