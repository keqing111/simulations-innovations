# planning 的单元测试构建：直接编译当前工作树的 scan/ 规划器 + 拓扑/原生
# 引擎源码，不依赖冻结包；check 先运行 C++ 穷举/一致性测试，再跑 Python
# 协议测试（网格、ref/opt 语义桥、篡改拒绝）。
CXX = g++
CXXFLAGS = -std=c++11 -O3 -DNOLOG -include cstring
INCLUDES = -I.. -I../algorithm -I../scheduler -I../topology -I../scan
# 原生引擎源码：矩阵、全交换分解、调度器。
NATIVE = ../algorithm/matrix.cpp ../algorithm/all2all.cpp ../scheduler/config.cpp ../scheduler/local.cpp ../scheduler/global.cpp
# 共享组件：scan 规划器与拓扑计价/匹配。
SHARED = ../scan/planner.cpp ../topology/model.cpp ../topology/matching.cpp
.PHONY: check
build:
	mkdir -p build
build/planner_test: tests/planner_test.cc $(SHARED) $(NATIVE) ../scan/planner.h | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< $(SHARED) $(NATIVE) -o $@
check: build/planner_test
	build/planner_test
	python3 -m unittest discover -s tests -v
