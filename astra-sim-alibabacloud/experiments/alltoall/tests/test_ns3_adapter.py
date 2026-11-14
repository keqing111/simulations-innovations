import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BINARY = REPO_ROOT / "bin" / "SimAI_simulator"
BASE_CONFIG = REPO_ROOT / "astra-sim-alibabacloud" / "inputs" / "config" / "SimAI.conf"


def _write_ns3_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    topology = root / "topology.txt"
    topology.write_text(
        "3 2 1 0 2 H100\n"
        "2\n"
        "0 2 900Gbps 0.000025ms 0\n"
        "1 2 900Gbps 0.000025ms 0\n",
        encoding="utf-8",
    )
    workload = root / "workload.txt"
    workload.write_text(
        "MICRO model_parallel_NPU_group: 1 ep: 2 all_gpus: 2\n"
        "1\n"
        "0 0 0 NONE 0 0 NONE 0 0 ALLTOALL 8192 0\n",
        encoding="utf-8",
    )
    schedule = root / "schedule.csv"
    schedule.write_text(
        "0,1,4096,0\n"
        "0,1,4096,50000\n",
        encoding="utf-8",
    )
    flow = root / "flow.txt"
    trace = root / "trace.txt"
    flow.write_text("0\n", encoding="utf-8")
    trace.write_text("0\n", encoding="utf-8")
    replacements = {
        "FLOW_FILE": flow,
        "TRACE_FILE": trace,
        "TRACE_OUTPUT_FILE": root / "trace.out",
        "FCT_OUTPUT_FILE": root / "fct.txt",
        "PFC_OUTPUT_FILE": root / "pfc.txt",
        "QLEN_MON_FILE": root / "qlen.txt",
        "BW_MON_FILE": root / "bw.txt",
        "RATE_MON_FILE": root / "rate.txt",
        "CNP_MON_FILE": root / "cnp.txt",
    }
    rendered = []
    for line in BASE_CONFIG.read_text(encoding="utf-8").splitlines():
        key = line.split(maxsplit=1)[0] if line.strip() else ""
        if key in replacements:
            rendered.append(f"{key} {replacements[key]}")
        elif key == "ENABLE_TRACE":
            rendered.append("ENABLE_TRACE 0")
        else:
            rendered.append(line)
    config = root / "ns3.conf"
    config.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    return topology, workload, schedule, config


class Ns3AdapterTests(unittest.TestCase):
    def test_v2_fixed_routes_background_hosts_are_not_collective_ranks(self):
        from simai_a2a.leafspine import build_leafspine,fixed_routes,write_fabric
        from simai_a2a.model import HardwareProfile,LinkModel,Schedule,ScheduledFlow
        from simai_a2a.execution import modeled_scaleup_schedule,write_native_commands,write_local_times
        import csv
        binary=Path(os.environ.get('SIMAI_NS3_BINARY',DEFAULT_BINARY))
        if not binary.is_file():self.skipTest('backend missing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,workload,schedule,config=_write_ns3_fixture(root)
            hw=HardwareProfile(16,4,LinkModel(100,.0023),LinkModel(700,.02),'v2',400,1)
            top=build_leafspine(hw,2);routes=fixed_routes(top);manifest=write_fabric(top,routes,root)
            workload.write_text('MICRO model_parallel_NPU_group: 1 ep: 64 all_gpus: 64\n1\n0 0 0 NONE 0 0 NONE 0 0 ALLTOALL 100000 0\n')
            schedule.write_text('0,32,100000,0\n32,0,100000,0\n')
            p=list(range(16));p[0],p[8]=8,0
            full=Schedule('test',[ScheduledFlow(0,0,32,100000,0,'inter',0),ScheduledFlow(1,32,0,100000,0,'inter',0)],None,
                          dict(n_stages=1,stage_records=[dict(mp=p)]))
            native=modeled_scaleup_schedule(full,hw);write_native_commands(native,root/'native.csv');write_local_times(native,root/'local.txt')
            (root/'background.csv').write_text('64,65,262144,70000,1000000000\n')
            done=subprocess.run([str(binary),'-t','0','-w',str(workload),'-n',str(root/'topology.txt'),'-c',str(config),'-s',str(schedule)],
                cwd=root,env={**os.environ,'SIMAI_NATIVE_FAST_FILE':str(root/'native.csv'),
                'SIMAI_NATIVE_LOCAL_TIMES':str(root/'local.txt'),'SIMAI_STATIC_ROUTES':str(root/'routes.csv'),
                'SIMAI_FOREGROUND_GPUS':'64','SIMAI_FOREGROUND_START_NS':'1000000',
                'SIMAI_BACKGROUND_FILE':str(root/'background.csv'),
                'SIMAI_NATIVE_WAN_PORTS':','.join(f'{a}:{b}' for a,b in manifest['gateway_wan_ports']),'AS_SEND_LAT':'0'},
                capture_output=True,text=True,timeout=30)
            self.assertEqual(done.returncode,0,done.stderr+done.stdout[-1000:])
            with (root/'native.csv.events.csv').open() as stream:events=list(csv.DictReader(stream))
            self.assertEqual(int(next(e['time_ns'] for e in events if e['event']=='begin')),1000000)
            self.assertEqual({int(r.split()[8]) for r in (root/'fct.txt').read_text().splitlines()},{0,1})
            self.assertTrue((root/'routes.csv.applied.csv').is_file())
            with (root/'native_wan.csv').open() as stream:packets=list(csv.DictReader(stream))
            self.assertTrue(any(int(r['kind'])==3 for r in packets),'background must traverse real WAN')
            self.assertTrue(any(int(r['payload_bytes'])>0 for r in packets))

    def test_modeled_local_tail_is_not_cut_off_at_last_network_ack(self):
        binary=Path(os.environ.get('SIMAI_NS3_BINARY',DEFAULT_BINARY))
        if not binary.is_file():self.skipTest('ns3 backend absent')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);topology,workload,schedule,config=_write_ns3_fixture(root)
            schedule.write_text('0,1,4096,0\n1,0,4096,0\n')
            commands=root/'native.csv'
            commands.write_text('2,1\n0,0,0,1,1\n1,0,1,0,0\n')
            local=root/'local.txt';local.write_text('2 1 1\n1000 5000\n2000 3000\n100000 200000\n')
            completed=subprocess.run([str(binary),'-t','0','-w',str(workload),'-n',str(topology),
                '-c',str(config),'-s',str(schedule)],cwd=root,
                env={**os.environ,'SIMAI_NATIVE_FAST_FILE':str(commands),
                     'SIMAI_NATIVE_LOCAL_TIMES':str(local),'AS_SEND_LAT':'0'},
                capture_output=True,text=True,timeout=20)
            self.assertEqual(completed.returncode,0,completed.stderr)
            import csv
            with Path(str(commands)+'.events.csv').open() as stream:
                events=list(csv.DictReader(stream))
            self.assertTrue(any(e['event']=='balance_done' for e in events),
                            'backend ignored modeled local durations')
            from simai_a2a.native_analysis import read_native_runtime
            from simai_a2a.model import Schedule,ScheduledFlow
            plan=Schedule('test',[ScheduledFlow(0,0,1,4096,0,'inter',0),
                                 ScheduledFlow(1,1,0,4096,0,'inter',0)],None,
                dict(execution_model='fast-nvidia-deps-modeled-scaleup',native_rank_count=2,n_stages=1,
                     local_times_ns=dict(gpus=1,balance=[1000,5000],intrinsic=[2000,3000],
                                         dispatch=[[100000,200000]])))
            result=read_native_runtime(plan,Path(str(commands)+'.events.csv'))
            self.assertTrue(result['local_duration_verified'])
            self.assertGreater(result['end_ns'],result['last_ack_ns'])

    def test_native_receive_credit_and_local_progress_are_network_driven(self):
        binary=Path(os.environ.get('SIMAI_NS3_BINARY',DEFAULT_BINARY))
        if not binary.is_file():self.skipTest('ns3 backend absent')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);topology,workload,schedule,config=_write_ns3_fixture(root)
            topology.write_text(topology.read_text().replace('1 2 900Gbps','1 2 100Gbps'))
            schedule.write_text('0,1,8000000,0\n1,0,4096,0\n0,1,4096,0\n1,0,4096,0\n')
            commands=root/'native.csv'
            commands.write_text('2,2\n0,0,0,1,1\n1,0,1,0,0\n0,1,2,3,1\n1,1,3,2,0\n')
            completed=subprocess.run([str(binary),'-t','0','-w',str(workload),'-n',str(topology),
                '-c',str(config),'-s',str(schedule)],cwd=root,
                env={**os.environ,'SIMAI_NATIVE_FAST_FILE':str(commands),
                     'SIMAI_NATIVE_WAN_PORTS':'2:2','AS_SEND_LAT':'0'},
                capture_output=True,text=True,timeout=20)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertTrue(Path(str(commands)+'.events.csv').is_file(), 'backend did not load native model')
            import csv
            with Path(str(commands)+'.events.csv').open() as stream:
                events=list(csv.DictReader(stream))
            def when(event,id):return int(next(e['time_ns'] for e in events if e['event']==event and int(e['flow_id'])==id))
            self.assertLess(when('data_ready',0),when('ack_done',0))
            self.assertGreater(when('credit_arrive',0),when('credit_send',0))
            self.assertLess(when('data_release',3),when('ack_done',0))
            rows=[line.split() for line in (root/'fct.txt').read_text().splitlines()]
            self.assertEqual(sorted((int(r[8]),int(r[4])) for r in rows),[(0,8000000),(1,4096),(2,4096),(3,4096)])
            with (root/'native_wan.csv').open() as stream:
                packets=list(csv.DictReader(stream))
            starts={int(p['start_ns']) for p in packets}
            with (root/'native_wan_queue.csv').open() as stream:
                queues=list(csv.DictReader(stream))
            previous_size=0;checked=False
            for q in queues:
                time=int(q['time_ns']);size=int(q['queue_bytes'])
                if previous_size==0 and time not in starts and any(
                        int(p['start_ns'])<time<int(p['start_ns'])+int(p['duration_ns']) for p in packets):
                    self.assertGreater(size,0,'first enqueue into empty busy queue must be observed after insertion')
                    checked=True
                previous_size=size
            self.assertTrue(checked,'fixture must exercise an empty busy egress queue')

    def test_standalone_stage_gate_rejects_duplicate_and_premature_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp)/'stage-gate-test'
            built = subprocess.run([
                'g++', '-std=c++14', '-I', str(REPO_ROOT/'astra-sim-alibabacloud/astra-sim/network_frontend/ns3'),
                str(Path(__file__).parent/'cpp/stage_barrier_test.cc'), '-o', str(executable)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            checked = subprocess.run([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_standalone_native_streams_buffer_reuse_and_virtual_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'native-runtime-test'
            checked=subprocess.run(['g++','-std=c++14','-I',
                str(REPO_ROOT/'astra-sim-alibabacloud/astra-sim/network_frontend/ns3'),
                str(Path(__file__).parent/'cpp/native_dependencies_test.cc'),'-o',str(executable)],
                capture_output=True,text=True)
            self.assertEqual(checked.returncode,0,checked.stderr)
            checked=subprocess.run([str(executable)],capture_output=True,text=True)
            self.assertEqual(checked.returncode,0,checked.stderr)

    def test_stage_barrier_waits_for_the_slowest_flow_and_skips_empty_stages(self):
        binary = Path(os.environ.get('SIMAI_NS3_BINARY', DEFAULT_BINARY))
        if not binary.is_file():
            self.skipTest(f'ns-3 binary not found: {binary}')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology, workload, schedule, config = _write_ns3_fixture(root)
            # Stage 0 has unequal independent flows; stage 3 may not overlap
            # either one. All eligibility offsets are deliberately identical.
            schedule.write_text('0,1,8000000,0\n1,0,4096,0\n0,1,4096,0\n')
            sidecar = root/'stages.csv'
            sidecar.write_text('0,0\n1,0\n2,3\n')
            completed = subprocess.run(
                [str(binary), '-t', '0', '-w', str(workload), '-n', str(topology),
                 '-c', str(config), '-s', str(schedule)], cwd=root,
                env={**os.environ, 'SIMAI_EXTERNAL_STAGE_FILE': str(sidecar)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rows = {int(r[8]): r for r in
                    (line.split() for line in (root/'fct.txt').read_text().splitlines())}
            self.assertEqual(set(rows), {0, 1, 2})
            slow_end = max(int(rows[i][5]) + int(rows[i][6]) for i in (0, 1))
            self.assertGreaterEqual(int(rows[2][5]), slow_end)
            self.assertLess(int(rows[1][5]) + int(rows[1][6]), slow_end)

    def test_external_release_later_than_signed_32_bit_nanoseconds(self):
        binary = Path(os.environ.get("SIMAI_NS3_BINARY", DEFAULT_BINARY))
        if not binary.is_file():
            self.skipTest(f"ns-3 binary not found: {binary}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology, workload, schedule, config = _write_ns3_fixture(root)
            schedule.write_text('0,1,4096,0\n0,1,4096,2200000000\n')
            completed = subprocess.run(
                [str(binary),'-t','0','-w',str(workload),'-n',str(topology),
                 '-c',str(config),'-s',str(schedule)], cwd=root,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
            self.assertEqual(completed.returncode,0,completed.stderr)
            rows = [line.split() for line in (root/'fct.txt').read_text().splitlines() if line]
            self.assertEqual(len(rows),2)
            starts = {int(row[8]): int(row[5]) for row in rows}
            self.assertEqual(starts[1]-starts[0],2200000000)

    def test_repeated_delayed_external_flows_finish_with_distinct_ids(self):
        binary = Path(os.environ.get("SIMAI_NS3_BINARY", DEFAULT_BINARY))
        if not binary.is_file():
            self.skipTest(f"ns-3 binary not found: {binary}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology, workload, schedule, config = _write_ns3_fixture(root)
            completed = subprocess.run(
                [
                    str(binary),
                    "-t", "1",
                    "-w", str(workload),
                    "-n", str(topology),
                    "-c", str(config),
                    "-s", str(schedule),
                ],
                cwd=root,
                env={**os.environ, "AS_LOG_LEVEL": "ERROR"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            rows = [line.split() for line in (root / "fct.txt").read_text().splitlines() if line]
            self.assertEqual(len(rows), 2, completed.stdout + completed.stderr)
            self.assertTrue(all(len(row) == 9 for row in rows), rows)
            self.assertEqual([int(row[8]) for row in rows], [0, 1])
            starts_ns = [int(row[5]) for row in rows]
            self.assertLessEqual(abs((starts_ns[1] - starts_ns[0]) - 50_000), 1)


if __name__ == "__main__":
    unittest.main()
