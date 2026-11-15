"""Literal mechanism fixtures; permutation enumeration is test-only."""
import itertools
import unittest

def topology(n,g=1,group=None,tau=10.,wan_bw=100.):
    pairs=[(s,d) for s in range(n) for d in range(n) if s!=d]
    assignment={pair:(group(*pair) if group else i) for i,pair in enumerate(pairs)}
    ids=sorted({v for v in assignment.values() if v is not None});offset={v:2*n+i for i,v in enumerate(ids)}
    return dict(n=n,g=g,resource_names=[f'tx:{s}' for s in range(n)]+[f'rx:{s}' for s in range(n)]+[f'wan:{v}' for v in ids],
        bandwidth_gbps=[400.]*(2*n)+[wan_bw]*len(ids),server_paths=[dict(
            resource_ids=[] if s==d else [s]+([] if assignment[s,d] is None else [offset[assignment[s,d]]])+[n+d],
            propagation_us=0. if s==d else tau) for s in range(n) for d in range(n)])

class ResourceBudgetValuationTests(unittest.TestCase):
    def test_small_q_can_clear_true_work_without_clearing_auxiliary_edge(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,evaluate_round
        R=[[0,8,1],[4,0,5],[5,1,3]];U=[0,8,1,4,0,2,5,1,0]
        a=evaluate_round(R,U,[1,2,0],2,prepare_resources(topology(3,tau=1.)))
        self.assertEqual(a['R_next'],[[0,6,1],[4,0,3],[3,1,3]])
        self.assertEqual(a['U_next'],[0,6,1,4,0,0,3,1,0])
        self.assertEqual((a['Z_R'],a['Z_U'],a['bytes']),(0,1,6))
        self.assertAlmostEqual(a['time_us'],1.00016)
        self.assertGreaterEqual(a['score'],0.)
        with self.assertRaises(ValueError):evaluate_round(R,U,[1,2,0],6,prepare_resources(topology(3)))

    def test_shared_wan_is_170us_but_independent_wans_are_90us(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,evaluate_round
        R=[[int(s!=d)*1000000 for d in range(4)] for s in range(4)];U=sum(R,[])
        shared=prepare_resources(topology(4,group=lambda s,d: int(s>=2)))
        independent=prepare_resources(topology(4))
        self.assertAlmostEqual(evaluate_round(R,U,[2,3,0,1],1000000,shared)['time_us'],170.)
        self.assertAlmostEqual(evaluate_round(R,U,[2,3,0,1],1000000,independent)['time_us'],90.)

    def test_real_pressure_does_not_count_padding_and_rejects_duplicate_resources(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,pressure
        res=prepare_resources(topology(3));self.assertEqual(max(pressure([0]*9,res)),0.)
        top=topology(3);top['server_paths'][1]['resource_ids'].insert(2,6)
        with self.assertRaises(ValueError):prepare_resources(top)

    def test_two_serial_wans_are_charged_once_each_not_added_as_time(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,evaluate_round,pressure
        top=topology(3);top['server_paths'][1]['resource_ids'].insert(2,7)
        res=prepare_resources(top);R=[[0,1000000,0],[0,0,1000000],[1000000,0,0]]
        U=[0,1000000,0,0,0,0,0,0,0]
        work=pressure(U,res)
        self.assertEqual((work[6],work[7]),(80.,80.))
        self.assertAlmostEqual(evaluate_round(R,U,[1,2,0],1000000,res)['time_us'],90.)

    def test_paths_with_different_first_wan_still_share_a_later_wan(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,evaluate_round,construct_round
        top=topology(4);shared=len(top['resource_names'])
        top['resource_names'].append('wan:shared-suffix');top['bandwidth_gbps'].append(100.)
        for index in (2,7):top['server_paths'][index]['resource_ids'].insert(-1,shared)
        R=[[0,0,1000000,0],[0,0,0,1000000],[1000000,0,0,0],[0,1000000,0,0]]
        U=[0,0,1000000,0,0,0,0,1000000]+[0]*8;res=prepare_resources(top)
        a=evaluate_round(R,U,[2,3,0,1],1000000,res)
        self.assertEqual(a['resource_bytes'][shared],2000000)
        self.assertAlmostEqual(a['time_us'],170.)
        self.assertIsNone(construct_round(R,U,res,(0,2),1000000,90.))

    def test_anchor_representatives_cover_all_tied_bottlenecks(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,pressure,select_anchors
        res=prepare_resources(topology(4));U=[0,8,0,0,8,0,0,0,0,6,0,2,0,0,8,0]
        work=pressure(U,res);critical={r for r,w in enumerate(work) if w==max(work)}
        anchors=select_anchors(U,res)
        represented={r for s,d in anchors for r in res['edges'][s*4+d]['path']}
        self.assertTrue(critical<=represented)

    def test_near_critical_wan_is_considered_within_its_own_service_window(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,select_anchors
        res=prepare_resources(topology(4,tau=53.))
        U=[0,4194304,0,0,4194304,0,0,0,0,3145728,0,1048576,0,0,4194304,0]
        anchors=select_anchors(U,res,near=True)
        self.assertIn((2,1),anchors)  # gap83.89us < its own304.66us service window
        self.assertNotIn((2,3),anchors)  # gap251.66us > its136.89us window

    def test_anchor_cap_is_five_not_its_full_eight_bytes(self):
        from simai_a2a.schedulers.resource_budget import anchored_cap
        R=[[0,8,1],[4,0,5],[5,1,3]]
        q,p=anchored_cap(R,(0,1));self.assertEqual(q,5);self.assertEqual(p,[1,2,0])

class ResourceBudgetConstructionTests(unittest.TestCase):
    def test_complete_matching_can_extend_an_infeasible_anchor_only_window(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,construct_round
        top=topology(4);shared=len(top['resource_names'])
        top['resource_names'].append('wan:shared-suffix');top['bandwidth_gbps'].append(100.)
        for index in (2,7):top['server_paths'][index]['resource_ids'].insert(-1,shared)
        R=[[0,0,1000000,0],[0,0,0,1000000],[1000000,0,0,0],[0,1000000,0,0]]
        U=[0,0,1000000,0,0,0,0,1000000]+[0]*8;res=prepare_resources(top)
        action=construct_round(R,U,res,(0,2),1000000,90.,relax=True)
        self.assertIsNotNone(action)
        self.assertEqual(action['p'][0],2)
        self.assertAlmostEqual(action['time_us'],170.)
        self.assertAlmostEqual(action['effective_H_us'],170.)

    def test_greedy_dead_end_does_not_discard_an_already_feasible_completion(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,construct_round
        q=2097152;R=[[q*int(s!=d) for d in range(4)] for s in range(4)]
        U=[v for row in R for v in row for _ in range(2)]
        res=prepare_resources(topology(4,2,group=lambda s,d:None if s//2==d//2 else s//2,tau=53.))
        action=construct_round(R,U,res,(0,2),q,388.54432)
        self.assertIsNotNone(action)
        self.assertEqual(action['p'][0],2)
        self.assertLessEqual(action['time_us'],388.54432)

    def test_dependency_frontier_preserves_independent_rank_overlap(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,evaluate_round,_advance_frontier
        res=prepare_resources(topology(4));R=[[1000000*int(s!=d) for d in range(4)] for s in range(4)]
        U=[0]*16;U[1]=1000000;U[11]=100000;U[8]=250000
        first=evaluate_round(R,U,[1,0,3,2],1000000,res)
        state=_advance_frontier(U,first,([0.]*4,[0.]*4),res)
        self.assertAlmostEqual(state[0][0],110.)
        self.assertAlmostEqual(state[0][2],38.)
        second=evaluate_round(first['R_next'],first['U_next'],[2,3,0,1],250000,res)
        state=_advance_frontier(first['U_next'],second,state,res)
        self.assertAlmostEqual(state[0][2],78.)  # not max(stage0)=110 + next flow

    def test_remaining_estimate_includes_serial_peer_work_not_only_nic_bytes(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,_remaining_time
        res=prepare_resources(topology(3));U=[0,1000000,1000000]+[0]*6
        self.assertAlmostEqual(_remaining_time(U,([0.]*3,[0.]*3),res),210.)
        self.assertAlmostEqual(_remaining_time(U,([20.,0.,0.],[0.]*3),res),220.)

    def test_future_coefficients_bound_the_number_of_remaining_messages(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,_remaining_time
        res=prepare_resources(topology(3));R=[[0,6,0],[2,0,4],[4,0,2]];U=[0,6,0,0,0,0,0,0,0]
        # All margins are6, but the largest possible future coefficient <=4.
        # Six bytes need >=2 messages: first credit10 + 2*(forward10+ACK10).
        self.assertAlmostEqual(_remaining_time(U,([0.]*3,[0.]*3),res,R),50.00048)

    def test_small_real_inventory_does_not_force_needless_auxiliary_fragments(self):
        from simai_a2a.schedulers.resource_budget import plan
        R=[[8*int(s!=d) for d in range(3)] for s in range(3)]
        U=[int(s!=d) for s in range(3) for d in range(3)]
        result=plan(dict(n=3,g=1,R=R,U=U),topology(3),allow_split=False)
        self.assertEqual(result['coefficients'],[8,8])
        self.assertEqual(len(result['trace']),2)

    def test_fixed_maximum_quantum_uses_the_actual_bottleneck_not_anchor_bytes(self):
        from simai_a2a.schedulers.resource_budget import plan
        R=[[6,8,0,0],[8,0,6,0],[0,6,0,8],[0,0,8,6]]
        U=[0,8,0,0,8,0,0,0,0,6,0,2,0,0,8,0]
        result=plan(dict(n=4,g=1,R=R,U=U),topology(4),maximum_quantum=True)
        residual=[r[:] for r in R]
        for p,q in zip(result['permutations'],result['coefficients']):
            oracle=max(min(residual[s][d] for s,d in enumerate(candidate))
                for candidate in itertools.permutations(range(4)))
            self.assertEqual(q,oracle)
            for s,d in enumerate(p):residual[s][d]-=q
        self.assertFalse(any(result['remaining_U']))

    def test_scc_force_matches_independent_small_graph_oracle(self):
        from simai_a2a.schedulers.resource_budget import _support,_components,_force
        R=[[0,8,1],[4,0,5],[5,1,3]];p=_support(R,1);pins={}
        for locked in (False,True):
            if locked:pins={0:p[0]}
            comp,graph=_components(R,1,p,pins);inv={d:s for s,d in enumerate(p)}
            for s in range(3):
                for d in range(3):
                    if s in pins or d in pins.values() or R[s][d]<1:continue
                    exists=any(pp[s]==d and all(pp[a]==b for a,b in pins.items()) and all(R[a][b]>=1 for a,b in enumerate(pp))
                               for pp in itertools.permutations(range(3)))
                    self.assertEqual(comp[s]==comp[inv[d]],exists)
                    if exists:
                        new=_force(p,s,d,graph);self.assertEqual(new[s],d);self.assertEqual(sorted(new),[0,1,2])
                        self.assertTrue(all(new[a]==b for a,b in pins.items()))

    def test_anchor_retained_and_hidden_completion_cannot_evade_budget(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,construct_round
        R=[[int(s!=d)*1000000 for d in range(4)] for s in range(4)];U=sum(R,[])
        action=construct_round(R,U,prepare_resources(topology(4)),(0,2),1000000,90.)
        self.assertIsNotNone(action);self.assertEqual(action['p'][0],2);self.assertLessEqual(action['time_us'],90.)
        self.assertIsNone(construct_round(R,U,prepare_resources(topology(4,group=lambda s,d:int(s>=2))),
                                        (0,2),1000000,90.))

    def test_handoff_quantity_is_event_derived_and_has_real_witness(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,propose_quanta,construct_round
        R=[[6,8,0,0],[8,0,6,0],[0,6,0,8],[0,0,8,6]]
        U=[0,8,0,0,8,0,0,0,0,6,0,2,0,0,8,0];res=prepare_resources(topology(4))
        proposals=propose_quanta(R,U,res,(0,1));small=next(c for c in proposals if c['reason']=='handoff')
        self.assertEqual(small['q'],2);self.assertEqual(small['witness'],(2,3))
        self.assertEqual(small['next_edge'],(2,1))
        action=construct_round(R,U,res,(0,1),small['q'],small['H_us'],small['witness'])
        self.assertIsNotNone(action);self.assertEqual(action['p'][2],3);self.assertGreater(action['Z_U'],0)

    def test_heterogeneous_handoff_window_rounds_up_to_cover_witness(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,propose_quanta,construct_round
        R=[[6,8,0,0],[8,0,6,0],[0,6,0,8],[0,0,8,6]]
        U=[0,8,0,0,8,0,0,0,0,6,0,2,0,0,8,0];top=topology(4)
        top['bandwidth_gbps'][top['server_paths'][11]['resource_ids'][1]]=60.
        res=prepare_resources(top);proposal=next(c for c in propose_quanta(R,U,res,(0,1)) if c['reason']=='handoff')
        self.assertEqual(proposal['q'],4)
        action=construct_round(R,U,res,(0,1),4,proposal['H_us'],proposal['witness'])
        self.assertIsNotNone(action);self.assertEqual(action['p'][2],3)

    def test_rejected_round_reports_budget_vs_support_failure(self):
        from simai_a2a.schedulers.resource_budget import prepare_resources,construct_round
        R=[[0,1000000],[1000000,0]];U=[0,1000000,1000000,0];res=prepare_resources(topology(2))
        reason={}
        self.assertIsNone(construct_round(R,U,res,(0,1),1000000,1.,diagnostics=reason))
        self.assertEqual(reason['code'],'pinned_budget_exceeded');self.assertGreater(reason['time_us'],reason['H_us'])
        reason={}
        self.assertIsNone(construct_round(R,U,res,(0,1),1000001,100.,diagnostics=reason))
        self.assertEqual(reason['code'],'no_support_completion')

    def test_empty_virtual_and_no_wan_cases_complete(self):
        from simai_a2a.schedulers.resource_budget import plan
        top=topology(2,group=lambda s,d:None)
        for R,U,want in [([[0,0],[0,0]],[0]*4,0),([[7,0],[0,7]],[0]*4,1),([[0,8],[8,0]],[0,8,8,0],1)]:
            result=plan(dict(n=2,g=1,R=R,U=U),top)
            self.assertEqual(len(result['trace']),want);self.assertEqual(sum(result['remaining_U']),0)
        with self.assertRaises(ValueError):plan(dict(n=2,g=1,R=[[0,1],[2,0]],U=[0]*4),top)

    def test_small_random_residuals_reconstruct_with_finite_progress(self):
        import random
        from simai_a2a.schedulers.resource_budget import plan
        rng=random.Random(881)
        for n in range(2,7):
            R=[[0]*n for _ in range(n)]
            for _ in range(n+1):
                p=list(range(n));rng.shuffle(p);q=rng.randint(1,8)*8192
                for s,d in enumerate(p):R[s][d]+=q
            U=[rng.randint(0,R[s][d]) if s!=d else 0 for s in range(n) for d in range(n) for u in range(2)]
            result=plan(dict(n=n,g=2,R=R,U=U),topology(n,2,group=lambda s,d:None if s//2==d//2 else (s//2,d//2)))
            rebuilt=[[0]*n for _ in range(n)]
            for p,q,step in zip(result['permutations'],result['coefficients'],result['trace']):
                self.assertGreater(step['Z_R']+step['Z_U'],0);self.assertEqual(sorted(p),list(range(n)))
                for s,d in enumerate(p):rebuilt[s][d]+=q
            self.assertEqual(rebuilt,R);self.assertFalse(any(result['remaining_U']))
            bound=sum(v>0 for row in R for v in row)+sum(v>0 for v in U)
            self.assertLessEqual(len(result['trace']),bound)
