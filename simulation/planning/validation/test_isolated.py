import importlib.util
from pathlib import Path
import unittest
import tempfile

spec=importlib.util.spec_from_file_location('isolated',Path(__file__).with_name('isolated.py'))
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


class IsolatedTests(unittest.TestCase):
    def test_protocol_is_fixed_and_balances_pair_block_order(self):
        methods=['F000-ref','F000-opt','maxmin-ref','maxmin-opt','spreadout-ref','spreadout-opt','X10-ref','X10-opt','SPM-ref','SPM-opt','A1-ref','A1-opt']
        self.assertEqual(v.orders(methods),[methods,list(reversed(methods))])
        cases=[dict(id=f'{n}-{kind}-{seed}-{w}-{d}',n=n,kind=kind,layout_seed=seed,wan_us=w,dc_count=d)
               for n in [24,48] for kind in ['balanced','zipf'] for seed in [760201,760203] for w in [50,5000] for d in [3,6]]
        selected=v.select(cases);self.assertEqual(len(selected),8)
        self.assertTrue(all(c['n']==48 and c['layout_seed']==760201 for c in selected))

    def test_override_requires_registered_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'registered build'):
                v.validate_binary(Path(tmp))


if __name__=='__main__':unittest.main()
