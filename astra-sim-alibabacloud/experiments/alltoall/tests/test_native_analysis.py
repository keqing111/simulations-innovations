import csv
from pathlib import Path
import tempfile
import unittest
from simai_a2a.model import Schedule,ScheduledFlow


class NativeAnalysisTests(unittest.TestCase):
    def test_background_queue_at_foreground_epoch_is_not_lost(self):
        from simai_a2a.native_analysis import link_idle_statistics
        result=link_idle_statistics(100,200,{}, {},[],[],[(90,1000),(150,0)])
        self.assertEqual(result['queued_idle_ns'],50)
        self.assertEqual(result['peak_queue_bytes'],1000)

    def test_cli_reanalysis_preserves_native_wan_evidence(self):
        import io,json,numpy as np
        from contextlib import redirect_stdout
        from simai_a2a.artifacts import write_matrix_artifact,write_schedule_artifact
        from simai_a2a.model import TrafficMatrix
        from simai_a2a.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario=root/'scenario';attempt=root/'attempt';attempt.mkdir()
            mm=write_matrix_artifact(TrafficMatrix(np.array([[0,100],[0,0]],dtype=np.int64)),scenario/'matrix')
            schedule=Schedule('fast-native-nvidia',[ScheduledFlow(0,0,1,100,0,'inter',0)],None,
                {'execution_model':'fast-nvidia-deps-ideal-scaleup','native_rank_count':2,
                 'native_gpus_per_server':1,'n_stages':1,'server_dc_ids':[0,1]})
            write_schedule_artifact(schedule,scenario/'schedules/fast-native-nvidia',
                matrix_sha256=mm['matrix_sha256'],hardware_sha256='test')
            (attempt/'fct.txt').write_text('0b000001 0b000101 1 100 100 5 20 20 0\n')
            for name in ('pfc.txt','qlen.txt','bw.txt','rate.txt','cnp.txt'):
                (attempt/name).write_text('')
            (attempt/'native.csv.events.csv').write_text('time_ns,event,flow_id,rank,stage\n'
                '0,begin,-1,-1,-1\n0,redis_done,-1,0,0\n0,credit_send,0,1,0\n'
                '5,credit_arrive,0,0,0\n5,data_release,0,0,0\n20,data_ready,0,1,0\n'
                '20,redis_done,0,1,0\n25,ack_done,0,0,0\n25,finished,-1,-1,-1\n')
            with redirect_stdout(io.StringIO()):
                result=main(['analyze','--scenario',str(scenario),'--algorithm','fast-native-nvidia',
                             '--attempt',str(attempt)])
            self.assertEqual(result,0)
            summary=json.loads((attempt/'summary.json').read_text())
            self.assertIn('native_wan',summary)
            self.assertEqual(summary['native_wan'],{'ports':[]})

    def test_epoch_and_local_streams_do_not_impose_global_barrier(self):
        from simai_a2a.native_analysis import read_native_runtime
        schedule=Schedule('fast-native-nvidia',[
            ScheduledFlow(0,0,1,100,0,'inter',0),ScheduledFlow(1,1,0,100,0,'inter',0),
            ScheduledFlow(2,0,1,100,0,'inter',1),ScheduledFlow(3,1,0,100,0,'inter',1)],None,
            {'native_rank_count':2,'n_stages':2})
        events=[(0,'begin',-1,-1,-1)]
        for id,src,dst,stage,cs,ca,release,rx,ack in [
            (0,0,1,0,0,5,5,20,25),(1,1,0,0,0,5,5,10,15),
            (2,0,1,1,20,25,25,30,35),(3,1,0,1,10,15,15,22,27)]:
            events += [(cs,'credit_send',id,dst,stage),(ca,'credit_arrive',id,src,stage),
                       (release,'data_release',id,src,stage),(rx,'data_ready',id,dst,stage),
                       (ack,'ack_done',id,src,stage),(rx,'redis_done',id,dst,stage)]
        events += [(35,'finished',-1,-1,-1)]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.csv'
            def write(rows):
                with path.open('w') as stream:
                    writer=csv.writer(stream);writer.writerow(['time_ns','event','flow_id','rank','stage'])
                    writer.writerows(sorted(rows,key=lambda r:r[0]))
            write(events)
            result=read_native_runtime(schedule,path)
            self.assertEqual(result['epoch_ns'],0)
            self.assertEqual(result['end_ns'],35)
            self.assertEqual(result['dependency_violations'],0)
            write([(t+20 if kind=='credit_arrive' and id==3 else t,kind,id,rank,stage)
                   for t,kind,id,rank,stage in events])
            with self.assertRaisesRegex(ValueError,'credit'):
                read_native_runtime(schedule,path)

    def test_idle_with_unreleased_data_is_separate_from_no_source_backlog(self):
        from simai_a2a.native_analysis import link_idle_statistics
        # The link is idle0..5 with eligible but unreleased data. Payload is
        # released at5, fully injected at7; wire busy5..9. Final9..10 has no
        # source backlog and must not be called scheduling-blocked idle.
        result=link_idle_statistics(0,10,{0:100},{0:5},[(7,0,100)],[(5,9)],[])
        self.assertEqual(result['busy_ns'],4)
        self.assertEqual(result['idle_ns'],6)
        self.assertEqual(result['dependency_only_idle_ns'],5)
        self.assertEqual(result['no_source_backlog_idle_ns'],1)


if __name__=='__main__':unittest.main()
