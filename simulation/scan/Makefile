CXX = g++
CXXFLAGS = -std=c++11 -O3 -DNOLOG -include cstring
INCLUDES = -I.. -I../algorithm -I../scheduler -I../topology -I../bottleneck -I.
NATIVE = ../algorithm/matrix.cpp ../algorithm/all2all.cpp ../scheduler/config.cpp ../scheduler/local.cpp ../scheduler/global.cpp
.PHONY: check
build:
	mkdir -p build
build/entry_test: tests/entry_test.cc $(NATIVE) ../scheduler/global.h | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< $(NATIVE) -o $@
build/planner_test: tests/planner_test.cc planner.cpp planner.h $(NATIVE) ../topology/model.cpp ../topology/matching.cpp ../bottleneck/cached.cpp | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< planner.cpp ../topology/model.cpp ../topology/matching.cpp ../bottleneck/cached.cpp $(NATIVE) -o $@
check: build/entry_test build/planner_test
	build/entry_test
	build/planner_test
	python3 -m unittest discover -s tests -v
