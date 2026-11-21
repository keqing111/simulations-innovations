import unittest
from resource_order.model import Message,Plan,Network,Link
from resource_order.scheduler import baseline
from resource_order.cyclic import groups,promote_group,critical_group_moves,order_by_work


class CyclicTests(unittest.TestCase):
    def test_shared_link_work_not_equal_to_max_message_volume(self):
        net=Network(4,[Link(0,4,100,0),Link(1,4,100,0),Link(4,5,10,0),Link(2,5,100,0),Link(3,5,100,0)],1)
        flows=[Message(s*4+d,s,d,1000) for s in range(4) for d in range(4) if s!=d]
        p=baseline(flows,'spreadout',4)
        resource,_=order_by_work(p,range(4),net)
        volume,_=order_by_work(p,range(4),net,resource=False)
        self.assertEqual(groups(resource,range(4))[1][0],2)
        self.assertEqual(groups(volume,range(4))[1],[1,2,3])
        self.assertEqual(resource.messages,p.messages)

    def test_cover_preserves_whole_messages_with_different_sizes(self):
        flows=[Message(s*4+d,s,d,10+s+d) for s in range(4) for d in range(4) if s!=d]
        p=baseline(flows,'spreadout',4)
        phase,order,buckets=groups(p,range(4));self.assertEqual(order,[1,2,3])
        for ids in buckets.values():
            fs=[f for f in flows if f.id in ids]
            self.assertEqual(len({f.src for f in fs}),4)
            self.assertEqual(len({f.dst for f in fs}),4)
        new=promote_group(p,range(4),1,3)
        self.assertEqual(groups(new,range(4))[1],[3,1,2])
        self.assertEqual(new.messages,p.messages)
        for s in range(4):
            old=[i for i in p.order if next(f for f in flows if f.id==i).src==s]
            changed=[i for i in new.order if next(f for f in flows if f.id==i).src==s]
            self.assertNotEqual(changed,old)

    def test_sparse_and_permuted_cover_and_duplicate_move_removal(self):
        f=[Message(0,0,2,10),Message(1,1,3,20),Message(2,0,1,30)]
        ranks=(0,2,1,3);p=baseline(f,'spreadout',4,rank_order=ranks)
        moves=critical_group_moves(p,ranks,[('send',0,0,2),('receive',1,0,2)])
        self.assertEqual(len(moves),1)
        new=promote_group(p,ranks,*moves[0][2:]);self.assertEqual(set(new.order),{0,1,2})

    def test_rejects_mixed_noncontiguous_groups(self):
        f=[Message(0,0,1,10),Message(1,0,2,10),Message(2,1,2,10)]
        with self.assertRaises(ValueError):groups(Plan.from_order(f,[0,1,2]),range(3))
