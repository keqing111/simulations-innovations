import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class ReferenceTests(unittest.TestCase):
    def test_linear_axes_calibrated_independently(self):
        from reference import decode_line
        values=decode_line([(10,120),(60,70),(110,20)],(10,20,110,120),(0,10),(0,20))
        self.assertEqual(values,[(0,0),(5,10),(10,20)])

    def test_actual_pdf_has_four_scale_curves_and_clipping_is_explicit(self):
        from reference import extract
        result=extract(ROOT/'assets/paper.pdf')
        self.assertEqual(set(result['scale']),{'fast_raw','fast_all','ideal','spreadout'})
        self.assertEqual(len(result['scale']['fast_raw']),20)
        self.assertEqual([v['x'] for v in result['scale']['fast_raw']],list(range(16,321,16)))
        self.assertTrue(result['clipped_non_grid'])
        self.assertNotIn(16,[v['x'] for v in result['scale']['ideal']])
        self.assertEqual(len(result['ratio']['fast_raw']),28)
        self.assertGreater(result['scale']['fast_raw'][0]['y'],70)
        self.assertLess(result['scale']['fast_raw'][-1]['y'],52)


if __name__=='__main__':unittest.main()
