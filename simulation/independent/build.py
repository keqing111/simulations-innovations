#!/usr/bin/env python3
"""Compile new policies against an immutable, hash-checked native snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

# ============================================================================
# independent/build.py：把新策略编译到“冻结的原生引擎快照”上。
#
# 设计动机：当前工作树里的 simulation/ 代码正在被批注/修改，不能作为实验
# 基线。因此本模块只信任 planning/results/final-96b18da3da36/source：
#   1. build.json 固定了引擎摘要 96b18da3da36 和每个源文件的哈希；
#   2. 构建前逐文件校验，任何冻结源码变化都直接报错；
#   3. 新代码只有 independent/policy.{h,cpp}、main.cc 和本文件本身；
#   4. 编译命令、编译器版本、自身源码哈希一起写入 build.json 作为身份。
# 构建产物按身份缓存：身份相同且二进制哈希未变时直接复用，避免重复编译；
# 身份不同则重新生成，但旧产物不会被删除或覆盖到其它运行目录。
#
# 符号重命名（-Dget_*_link_info=...）用于把冻结引擎里的链接工厂替换成
# reproduction/link_profile 的实现，同时避免与 config.cpp 的默认工厂重名。
# ============================================================================

ROOT = Path(__file__).resolve().parent
# 冻结引擎目录；其 build.json 由 planning 研究包生成并随包归档。
FROZEN = ROOT.parent / 'planning/results/final-96b18da3da36'
# 与冻结引擎一致的编译选项：C++11、O3、关闭日志、强制包含 cstring
# （冻结源码中有未显式 include 的 memset/memcpy 使用）。
FLAGS = ['-std=c++11', '-O3', '-DNOLOG', '-include', 'cstring']


def sha(path):
    # 统一用 SHA-256 文件内容摘要做身份/完整性检查，不依赖 mtime。
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(output=None):
    # output 默认是 independent/build；调用方可以给每个运行单独目录，
    # 使缓存与实验数据一起归档。
    output = Path(output or ROOT / 'build').resolve()
    record = json.loads((FROZEN / 'build.json').read_text())
    # 只接受这一个冻结摘要前缀；换引擎必须显式修改常量并重新登记，
    # 防止“当前工作树刚好也能编译”被误当成同一基线。
    if not record['identity']['digest'].startswith('96b18da3da36'):
        raise ValueError('wrong frozen engine identity')
    source = FROZEN / 'source'
    # 逐文件校验冻结源码，而不是只信 build.json 自身。
    for name, digest in record['identity']['files'].items():
        if sha(source / name) != digest:
            raise ValueError('frozen engine source changed: ' + name)
    # 自身源码身份：policy.h/policy.cpp/main.cc 决定策略与接线，build.py
    # 决定编译方式；任一变化都应触发重新编译和新运行目录。
    own = {p.name: sha(p) for p in [ROOT/'policy.h', ROOT/'policy.cpp', ROOT/'main.cc', ROOT/'build.py']}
    compiler = subprocess.check_output(['g++', '--version'], text=True).splitlines()[0]
    identity = dict(engine=record['identity'], own=own, flags=FLAGS, compiler=compiler)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'build.json'
    binary = output / 'native_independent'
    # 缓存命中条件：身份完全一致 + 二进制仍存在 + 二进制哈希与清单一致。
    # 只看身份不够，因为产物可能被删除/替换。
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if old['identity'] == identity and binary.exists() and sha(binary) == old['binary_sha256']:
            return binary
    # 头文件搜索路径：先本目录（policy.h/main.cc 的本地头），再冻结引擎的
    # 根/algorithm/scheduler/topology/scan/reproduction 子目录。
    include = ['-I'+str(ROOT), *['-I'+str(source/p) for p in ['', 'algorithm', 'scheduler', 'topology', 'scan', 'reproduction']]]
    # 三条编译命令：
    #   1. config.cpp 使用“上游默认”链接工厂（重命名为 upstream_*）；
    #   2. link_profile.cc 使用“论文档案”工厂（重命名为 profile_*）；
    #   3. 其余冻结引擎源码 + 新策略源码 + 两个对象文件链接成最终二进制。
    # 分开编译两个 config 实现是为了让同一份冻结引擎支持不同链路档案，
    # 而不在运行时用全局开关改变语义。
    commands = [
        ['g++', *FLAGS, *include, '-Dget_inter_link_info=upstream_get_inter_link_info',
         '-Dget_intra_link_info=upstream_get_intra_link_info', '-c', str(source/'scheduler/config.cpp'), '-o', str(output/'config.o')],
        ['g++', *FLAGS, *include, '-Dget_inter_link_info=profile_get_inter_link_info',
         '-Dget_intra_link_info=profile_get_intra_link_info', '-c', str(source/'reproduction/link_profile.cc'), '-o', str(output/'profile.o')],
        # 主干：冻结引擎的矩阵/全交换/调度器/拓扑/扫描源码 + 新策略与 CLI。
        ['g++', *FLAGS, *include, *[str(source/p) for p in ['algorithm/matrix.cpp', 'algorithm/all2all.cpp',
         'scheduler/local.cpp', 'scheduler/global.cpp', 'test.cpp', 'topology/model.cpp', 'topology/matching.cpp', 'scan/planner.cpp']],
         str(ROOT/'policy.cpp'), str(ROOT/'main.cc'), str(output/'config.o'), str(output/'profile.o'), '-lpthread', '-o', str(binary)]]
    for command in commands:
        done = subprocess.run(command, text=True, capture_output=True)
        if done.returncode:
            raise RuntimeError(done.stderr)
    # 只有全部命令成功才写清单；清单记录编译命令和二进制哈希，供 audit
    # 重放/校验。失败时不会留下“看起来完整”的清单。
    manifest.write_text(json.dumps(dict(identity=identity, commands=commands, binary_sha256=sha(binary)), sort_keys=True, indent=2)+'\n')
    return binary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(build(args.output))
