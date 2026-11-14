import json
import io
import tempfile
import unittest
import os
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

from simai_a2a.artifacts import write_matrix_artifact, write_schedule_artifact
from simai_a2a.cli import main as cli_main
from simai_a2a.model import Schedule, ScheduledFlow, TrafficMatrix
from simai_a2a.runner import run_scenario


FAKE_SUCCESS = r'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path
args = sys.argv[1:]
values = {args[i]: args[i + 1] for i in range(0, len(args), 2)}
Path("argv.json").write_text(json.dumps(args))
config = {}
for line in Path(values["-c"]).read_text().splitlines():
    parts = line.split(maxsplit=1)
    if len(parts) == 2:
        config[parts[0]] = parts[1]
rows = []
for flow_id, line in enumerate(Path(values["-s"]).read_text().splitlines()):
    src, dst, size, start = map(int, line.split(","))
    ip = lambda node: f"{0x0B000001 + ((node // 256) << 16) + ((node % 256) << 8):08x}"
    rows.append(f"{ip(src)} {ip(dst)} {10000 + flow_id} 100 {size} {1000 + start} 10 12 {flow_id}")
Path(config["FCT_OUTPUT_FILE"]).write_text("\n".join(rows) + "\n")
Path(config["PFC_OUTPUT_FILE"]).write_text("")
'''


def _scenario(root: Path) -> tuple[Path, Path]:
    scenario = root / "scenario"
    matrix = TrafficMatrix(np.array([[0, 64], [64, 0]], dtype=np.int64))
    matrix_manifest = write_matrix_artifact(matrix, scenario / "matrix")
    schedule = Schedule(
        "direct-concurrent",
        [
            ScheduledFlow(0, 0, 1, 64, 0, "direct", 0),
            ScheduledFlow(1, 1, 0, 64, 0, "direct", 0),
        ],
        20,
    )
    write_schedule_artifact(
        schedule,
        scenario / "schedules" / "direct-concurrent",
        matrix_sha256=matrix_manifest["matrix_sha256"],
        hardware_sha256="hardware",
    )
    (scenario / "workload.csv").write_text(
        "MICRO model_parallel_NPU_group: 1 ep: 2 all_gpus: 2\n"
        "1\n0 0 0 NONE 0 0 NONE 0 0 ALLTOALL 64 0\n",
        encoding="utf-8",
    )
    topology = root / "topology.txt"
    topology.write_text("topology\n", encoding="utf-8")
    return scenario, topology


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


class RunnerTests(unittest.TestCase):
    def test_v2_routes_background_are_copied_exported_and_fingerprinted(self):
        from simai_a2a.runner import execution_fingerprint,DEFAULT_CONFIG_TEMPLATE
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario,topology=_scenario(root)
            (scenario/'routes.csv').write_text('0,1,1\n')
            (scenario/'background.csv').write_text('2,3,262144,70000,1000000000\n')
            (scenario/'scenario_manifest.json').write_text(json.dumps(dict(topology=dict(
                foreground_gpu_count=2,foreground_start_ns=1000000))))
            binary=_executable(root/'fake.py',FAKE_SUCCESS+'\nimport os\nPath("v2-env.json").write_text(json.dumps({k:os.environ.get(k) for k in ["SIMAI_STATIC_ROUTES","SIMAI_BACKGROUND_FILE","SIMAI_FOREGROUND_GPUS","SIMAI_FOREGROUND_START_NS"]}))\n')
            sig=lambda:execution_fingerprint(scenario,'direct-concurrent',topology,binary,DEFAULT_CONFIG_TEMPLATE,0)
            old=sig();(scenario/'routes.csv').write_text('0,1,0\n');self.assertNotEqual(old,sig())
            result=run_scenario(scenario,'direct-concurrent',topology,binary,timeout_s=2)
            self.assertTrue(result['success'],result)
            attempt=Path(result['attempt_dir']);env=json.loads((attempt/'v2-env.json').read_text())
            self.assertEqual(env['SIMAI_FOREGROUND_GPUS'],'2');self.assertEqual(env['SIMAI_FOREGROUND_START_NS'],'1000000')
            for name,key in [('routes.csv','SIMAI_STATIC_ROUTES'),('background.csv','SIMAI_BACKGROUND_FILE')]:
                self.assertEqual(Path(env[key]),attempt/name)
                self.assertEqual((attempt/name).read_bytes(),(scenario/name).read_bytes())

    def test_ns3_attribute_environment_changes_fingerprint_and_override_is_stable(self):
        from simai_a2a.runner import execution_fingerprint,DEFAULT_CONFIG_TEMPLATE
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario,topology=_scenario(root);binary=_executable(root/'fake.py',FAKE_SUCCESS)
            signature=lambda **kw:execution_fingerprint(scenario,'direct-concurrent',topology,binary,DEFAULT_CONFIG_TEMPLATE,0,**kw)
            with patch.dict(os.environ,{'NS_ATTRIBUTE_DEFAULT':'ns3::BEgressQueue::MaxBytes=1000'}):first=signature()
            with patch.dict(os.environ,{'NS_ATTRIBUTE_DEFAULT':'ns3::BEgressQueue::MaxBytes=2000'}):second=signature()
            self.assertNotEqual(first,second)
            override={'NS_ATTRIBUTE_DEFAULT':'ns3::BEgressQueue::MaxBytes=3000','NS_GLOBAL_VALUE':None}
            with patch.dict(os.environ,{'NS_ATTRIBUTE_DEFAULT':'ambient1'}):first=signature(ns3_env=override)
            with patch.dict(os.environ,{'NS_ATTRIBUTE_DEFAULT':'ambient2'}):second=signature(ns3_env=override)
            self.assertEqual(first,second)

    def test_ns3_environment_override_matches_recorded_actual_child_values(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'NS_GLOBAL_VALUE':'ambient'}):
            root=Path(tmp);scenario,topology=_scenario(root)
            binary=_executable(root/'fake.py',FAKE_SUCCESS+'\nimport os\nPath("env.json").write_text(json.dumps({k:os.environ.get(k) for k in ["NS_ATTRIBUTE_DEFAULT","NS_GLOBAL_VALUE"]}))\n')
            env={'NS_ATTRIBUTE_DEFAULT':'ns3::BEgressQueue::MaxBytes=3000','NS_GLOBAL_VALUE':None}
            r=run_scenario(scenario,'direct-concurrent',topology,binary,timeout_s=2,ns3_env=env)
            self.assertTrue(r['success'],r)
            self.assertEqual(json.loads((Path(r['attempt_dir'])/'env.json').read_text()),env)
            self.assertEqual(r['ns3_environment'],env)
            for bad in [{'HOME':'x'},{'NS_GLOBAL_VALUE':4}]:
                with self.assertRaises(ValueError):run_scenario(scenario,'direct-concurrent',topology,binary,timeout_s=2,ns3_env=bad)

    def test_execution_fingerprint_changes_when_ns3_shared_library_changes(self):
        from simai_a2a.runner import execution_fingerprint,DEFAULT_CONFIG_TEMPLATE
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario,topology=_scenario(root)
            (root/'build/scratch').mkdir(parents=True);(root/'build/lib').mkdir()
            binary=_executable(root/'build/scratch/backend','#!/bin/sh\nexit 0\n')
            library=root/'build/lib/libns3.test-point-to-point-debug.so';library.write_bytes(b'old library')
            signature=lambda:execution_fingerprint(scenario,'direct-concurrent',topology,binary,DEFAULT_CONFIG_TEMPLATE,0)
            first=signature();library.write_bytes(b'new library')
            self.assertNotEqual(first,signature())

    def test_execution_fingerprint_tracks_prepared_metadata_and_all_execution_inputs(self):
        from simai_a2a.runner import execution_fingerprint,DEFAULT_CONFIG_TEMPLATE
        from simai_a2a.artifacts import read_schedule_artifact
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario,topology=_scenario(root)
            binary=_executable(root/'backend','#!/bin/sh\nexit 0\n')
            config=root/'template.conf';config.write_bytes(DEFAULT_CONFIG_TEMPLATE.read_bytes())
            signature=lambda:execution_fingerprint(scenario,'direct-concurrent',topology,binary,config,0)
            original=signature()
            schedule,manifest=read_schedule_artifact(scenario/'schedules/direct-concurrent')
            schedule.metadata['native_global_send_barrier']=True
            write_schedule_artifact(schedule,scenario/'schedules/direct-concurrent',
                matrix_sha256=manifest['matrix_sha256'],hardware_sha256=manifest['hardware_sha256'])
            self.assertNotEqual(signature(),original)
            previous=signature();topology.write_text('changed topology\n')
            self.assertNotEqual(signature(),previous)
            previous=signature();config.write_text(config.read_text()+'\nCC_MODE 7\n')
            self.assertNotEqual(signature(),previous)
            previous=signature();binary.write_text('#!/bin/sh\nexit 1\n')
            self.assertNotEqual(signature(),previous)

    def test_ideal_local_only_schedule_finishes_without_network_execution(self):
        from simai_a2a.execution import STAGE_BARRIER_MODEL
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario, topology = _scenario(root)
            (scenario/'scenario_manifest.json').write_text(json.dumps({'topology': {
                'gateway_wan_ports': [[33, 2]], 'gateway_wan_port_capacity_gbps': 100}}))
            mm = json.loads((scenario/'matrix/matrix_manifest.json').read_text())
            write_schedule_artifact(Schedule('direct-concurrent', [], None,
                                    {'execution_model': STAGE_BARRIER_MODEL}),
                scenario/'schedules/direct-concurrent',
                matrix_sha256=mm['matrix_sha256'], hardware_sha256='hardware')
            binary = _executable(root/'must-not-run', '#!/bin/sh\nexit 99\n')
            result = run_scenario(scenario, 'direct-concurrent', topology, binary, timeout_s=2)
            self.assertTrue(result['success'], result)
            self.assertEqual(result['summary']['actual_makespan_ns'], 0)
            self.assertEqual(result['summary']['flow_count'], 0)

    def test_cli_reanalysis_preserves_wan_monitor_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario, topology = _scenario(root)
            (scenario/'scenario_manifest.json').write_text(json.dumps({'topology':{
                'gateway_ids':[33], 'gateway_wan_ports':[[33,2]],
                'gateway_wan_port_capacity_gbps':100}}))
            binary = _executable(root/'fake.py', FAKE_SUCCESS +
                '\nPath(config["BW_MON_FILE"]).write_text("time, node_id, port_id, bandwidth\\n1000000,33,2,50\\n")\n')
            run = run_scenario(scenario,'direct-concurrent',topology,binary,timeout_s=2)
            self.assertTrue(run['success'], run)
            attempt = Path(run['attempt_dir'])
            expected = json.loads((attempt/'summary.json').read_text())['wan']
            with redirect_stdout(io.StringIO()):
                status = cli_main(['analyze','--scenario',str(scenario),
                    '--algorithm','direct-concurrent','--attempt',str(attempt)])
            self.assertEqual(status,0)
            self.assertEqual(json.loads((attempt/'summary.json').read_text())['wan'],expected)

    def test_wan_monitor_rejects_unobserved_prefix(self):
        from simai_a2a.analysis import add_wan_statistics
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'ns3.conf'
            config.write_text('MON_START 2\nMON_END 100\nBW_MON_INTERVAL 5\n')
            with self.assertRaisesRegex(ValueError, 'MON_START'):
                add_wan_statistics({'collective_epoch_ns':0,'actual_makespan_ns':20000},
                                   config, {'gateway_wan_ports': [[33,2]],
                                            'gateway_wan_port_capacity_gbps':100})
    def test_run_and_analyze_cli_subcommands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario, topology = _scenario(root)
            binary = _executable(root / "fake.py", FAKE_SUCCESS)
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main(
                    [
                        "run",
                        "--scenario", str(scenario),
                        "--algorithm", "direct-concurrent",
                        "--topology", str(topology),
                        "--binary", str(binary),
                        "--timeout", "2",
                    ]
                )
            self.assertEqual(status, 0)
            result = json.loads(output.getvalue())
            attempt = Path(result["attempt_dir"])
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main(
                    [
                        "analyze",
                        "--scenario", str(scenario),
                        "--algorithm", "direct-concurrent",
                        "--attempt", str(attempt),
                    ]
                )
            self.assertEqual(status, 0)
            self.assertTrue((attempt / "summary.json").is_file())

    def test_success_uses_local_files_and_new_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario, topology = _scenario(root)
            binary = _executable(root / "fake.py", FAKE_SUCCESS)

            first = run_scenario(scenario, "direct-concurrent", topology, binary, timeout_s=2)
            second = run_scenario(scenario, "direct-concurrent", topology, binary, timeout_s=2)

            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "success")
            first_dir = Path(first["attempt_dir"])
            second_dir = Path(second["attempt_dir"])
            self.assertNotEqual(first_dir, second_dir)
            self.assertTrue(first_dir.name.endswith("0001"))
            self.assertTrue(second_dir.name.endswith("0002"))
            argv = json.loads((first_dir / "argv.json").read_text())
            self.assertEqual(set(argv[::2]), {"-n", "-c", "-w", "-s"})
            for flag in ("-n", "-c", "-w", "-s"):
                self.assertEqual(Path(argv[argv.index(flag) + 1]).parent, first_dir)
            self.assertTrue((first_dir / "summary.json").is_file())
            self.assertEqual(first["summary"]["flow_count"], 2)

    def test_optional_ns3_thread_count_is_passed_to_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario, topology = _scenario(root)
            binary = _executable(root / "fake.py", FAKE_SUCCESS)

            result = run_scenario(
                scenario,
                "direct-concurrent",
                topology,
                binary,
                timeout_s=2,
                ns3_threads=0,
            )

            argv = json.loads((Path(result["attempt_dir"]) / "argv.json").read_text())
            self.assertEqual(argv[argv.index("-t") + 1], "0")
            self.assertEqual(result["ns3_threads"], 0)

    def test_timeout_and_incomplete_output_are_non_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario, topology = _scenario(root)
            sleeper = _executable(
                root / "sleep.py",
                "#!/usr/bin/env python3\nimport time\ntime.sleep(2)\n",
            )
            timeout = run_scenario(
                scenario, "direct-concurrent", topology, sleeper, timeout_s=0.05
            )
            self.assertEqual(timeout["status"], "timeout")
            self.assertFalse(timeout["success"])

            empty = _executable(root / "empty.py", "#!/usr/bin/env python3\n")
            incomplete = run_scenario(
                scenario, "direct-concurrent", topology, empty, timeout_s=2
            )
            self.assertEqual(incomplete["status"], "incomplete")
            self.assertFalse(incomplete["success"])


if __name__ == "__main__":
    unittest.main()
