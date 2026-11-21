import unittest
from resource_order.model import Message, EndpointGate
from resource_order.pairwise_control import pairwise_xor
from resource_order.simai import encode


class PairwiseControlTests(unittest.TestCase):
    def messages(self, n):
        return tuple(Message(i*n+j, i, j, 1+i*n+j)
                     for i in range(n) for j in range(n) if i != j)

    def test_formula_and_coverage(self):
        for n in (2, 4, 8, 128):
            messages = self.messages(n)
            plan = pairwise_xor(messages, n)
            expected = tuple(i*n+(i^step) for step in range(1,n) for i in range(n))
            self.assertEqual(plan.order, expected)
            self.assertEqual(plan.messages, messages)
            for step in range(1,n):
                group = plan.order[(step-1)*n:step*n]
                self.assertEqual(len({i//n for i in group}), n)
                self.assertEqual(len({i%n for i in group}), n)

    def test_relabeling(self):
        ranks = (0, 2, 4, 6, 1, 3, 5, 7)
        plan = pairwise_xor(self.messages(8), 8, ranks)
        expected = tuple(ranks[i]*8+ranks[i^step] for step in range(1,8) for i in range(8))
        self.assertEqual(plan.order, expected)

    def test_sparse_and_stage_free_abi(self):
        messages = self.messages(8)[::3]
        plan = pairwise_xor(messages, 8)
        gate = EndpointGate(plan)
        while not gate.finished():
            ready = gate.ready()
            self.assertTrue(ready)
            for ident in ready:
                gate.release(ident); gate.received(ident); gate.acknowledged(ident)
        _, commands, mapping = encode(plan, 8)
        self.assertEqual(commands.splitlines()[0].split(',')[-2:], ['0','0'])
        self.assertEqual(set(mapping.values()), {f.id for f in messages})

    def test_invalid_inputs(self):
        for n in (0, 1, 3, True):
            with self.assertRaises(ValueError): pairwise_xor((), n)
        for ranks in ((0, 1), (0, 1, 2, 2), (True,0,2,3)):
            with self.assertRaises(ValueError): pairwise_xor((), 4, ranks)
        with self.assertRaises(ValueError): pairwise_xor((Message(0,0,4,1),),4)
        duplicate = (Message(0,0,1,1), Message(1,0,1,2))
        with self.assertRaises(ValueError): pairwise_xor(duplicate,4)


if __name__ == '__main__': unittest.main()
